"""Dashboard routes — cameras, presets, zones, snapshots, incidents.

Mounted on the public Flask app at port 5050. Read-and-write surface for
the operator UI. The frontend is plain HTML/JS served from
`itips/api/static/dashboard/`.

Authentication is intentionally absent at POC; the dashboard is bound to
the site LAN behind operator-managed access. Phase 1 adds the same
bearer/mTLS surface the inbound 8443 API already enforces.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from config.settings import settings
from itips.behaviour.registration import reference_path
from itips.behaviour.zones import get_store as get_zone_store

logger = logging.getLogger(__name__)

_STATIC_ROOT = Path(__file__).resolve().parent / "static" / "dashboard"

# In-memory candidate-reference cache: each /api/snapshot stashes the
# served frame here, keyed by (camera_id, preset_id). On the next PUT
# /api/zones for that key we promote the stash to a JPEG on disk and the
# camera worker picks it up via mtime polling.
_pending_refs: dict[tuple[int, str], tuple[float, np.ndarray]] = {}
_pending_lock = threading.Lock()
_PENDING_TTL_SECONDS = 600  # one snapshot is good as a candidate for 10 min


def register_dashboard(app: Flask, *, frame_bus, preset_registry, ptz_controllers) -> None:
    """Wire all dashboard routes onto the given Flask app."""

    @app.get("/dashboard")
    def dashboard_index():  # noqa: ANN202
        return send_from_directory(_STATIC_ROOT, "index.html")

    @app.get("/dashboard/<path:filename>")
    def dashboard_assets(filename: str):  # noqa: ANN202
        return send_from_directory(_STATIC_ROOT, filename)

    # ─── cameras + presets ─────────────────────────────────────────

    @app.get("/api/cameras")
    def list_cameras():  # noqa: ANN202
        active = settings.cameras.active()
        out = []
        for cam_id in sorted(active.keys()):
            ptz = ptz_controllers.get(cam_id) if ptz_controllers else None
            out.append({
                "camera_id": cam_id,
                "active_preset": preset_registry.active(cam_id),
                "presets": preset_registry.list_for(cam_id),
                "ptz_connected": bool(ptz and getattr(ptz, "is_connected", False)),
                "ptz_configured": preset_registry.has_definitions(cam_id),
            })
        return jsonify({"cameras": out})

    @app.post("/api/cameras/<int:camera_id>/preset")
    def set_preset(camera_id: int):  # noqa: ANN202
        body = request.get_json(silent=True) or {}
        preset_id = body.get("preset_id")
        if not preset_id:
            return jsonify({"ok": False, "error": "preset_id required"}), 400
        if not preset_registry.set_active(camera_id, str(preset_id)):
            return jsonify({"ok": False, "error": f"unknown preset '{preset_id}'"}), 404
        ptz = ptz_controllers.get(camera_id) if ptz_controllers else None
        if ptz and getattr(ptz, "is_connected", False):
            method = getattr(ptz, "go_to_preset", None) or getattr(ptz, "apply_override", None)
            if callable(method):
                try:
                    method({"preset_id": preset_id})
                except Exception:
                    logger.exception("PTZ preset move failed for cam %d", camera_id)
        return jsonify({"ok": True, "active_preset": preset_registry.active(camera_id)})

    # ─── zones ─────────────────────────────────────────────────────

    @app.get("/api/zones/<int:camera_id>")
    def get_zones(camera_id: int):  # noqa: ANN202
        preset_id = request.args.get("preset_id") or preset_registry.active(camera_id)
        zones = get_zone_store().for_camera_preset(camera_id, preset_id)
        return jsonify({
            "camera_id": camera_id,
            "preset_id": preset_id,
            "zones": {name: [list(p) for p in poly] for name, poly in zones.items()},
        })

    @app.put("/api/zones/<int:camera_id>")
    def put_zones(camera_id: int):  # noqa: ANN202
        body = request.get_json(silent=True) or {}
        preset_id = str(body.get("preset_id") or preset_registry.active(camera_id))
        zones_payload = body.get("zones") or {}
        try:
            cleaned = _validate_zones(zones_payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        get_zone_store().replace_preset(camera_id, preset_id, cleaned)
        ref_written = _promote_reference(camera_id, preset_id, frame_bus)
        return jsonify({
            "ok": True,
            "camera_id": camera_id,
            "preset_id": preset_id,
            "zone_count": len(cleaned),
            "reference_written": ref_written,
        })

    # ─── snapshots ─────────────────────────────────────────────────

    @app.get("/api/snapshot/<int:camera_id>")
    def snapshot(camera_id: int):  # noqa: ANN202
        snap = frame_bus.latest(camera_id)
        if snap is None or snap.raw is None:
            abort(404)
        clean = request.args.get("clean", "1") != "0"
        frame = snap.raw if clean else (snap.annotated if snap.annotated is not None else snap.raw)
        preset_id = request.args.get("preset_id") or snap.preset_id

        # Stash the *raw* frame as a candidate reference; if the operator
        # saves zones soon after, this is the frame those zones live in.
        with _pending_lock:
            _pending_refs[(camera_id, preset_id)] = (time.monotonic(), snap.raw.copy())

        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            abort(500)
        return Response(
            encoded.tobytes(),
            mimetype="image/jpeg",
            headers={
                "X-Frame-Width": str(frame.shape[1]),
                "X-Frame-Height": str(frame.shape[0]),
                "X-Preset-Id": preset_id,
                "Cache-Control": "no-store",
            },
        )

    # ─── incidents ─────────────────────────────────────────────────

    @app.get("/api/incidents")
    def list_incidents():  # noqa: ANN202
        root = Path(settings.evidence.store_path) / "incidents"
        out = []
        if root.exists():
            for entry in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not entry.is_dir():
                    continue
                summary = _incident_summary(entry)
                if summary:
                    out.append(summary)
        return jsonify({"incidents": out})

    @app.get("/api/incidents/<incident_id>")
    def get_incident(incident_id: str):  # noqa: ANN202
        package = _safe_incident_dir(incident_id)
        if package is None:
            abort(404)
        return jsonify({
            "incident_id": incident_id,
            "metadata": _read_json(package / "incident_metadata.json"),
            "manifest": _read_json(package / "manifest.json"),
            "signature": _read_json(package / "signature.sha256"),
            "files": [f.name for f in sorted(package.rglob("*")) if f.is_file()],
        })

    @app.get("/api/incidents/<incident_id>/files/<path:filename>")
    def download_incident_file(incident_id: str, filename: str):  # noqa: ANN202
        package = _safe_incident_dir(incident_id)
        if package is None:
            abort(404)
        candidate = (package / filename).resolve()
        if not candidate.is_file() or package not in candidate.parents:
            abort(404)
        return send_file(candidate, as_attachment=True)


# ─── helpers ─────────────────────────────────────────────────────────


def _validate_zones(payload: Any) -> dict[str, list[tuple[float, float]]]:
    if not isinstance(payload, dict):
        raise ValueError("zones must be an object")
    out: dict[str, list[tuple[float, float]]] = {}
    for name, polygon in payload.items():
        if not isinstance(name, str) or not name:
            raise ValueError("zone names must be non-empty strings")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError(f"zone '{name}' needs ≥ 3 points")
        cleaned: list[tuple[float, float]] = []
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"zone '{name}' point must be [x, y]")
            cleaned.append((float(point[0]), float(point[1])))
        out[name] = cleaned
    return out


def _safe_incident_dir(incident_id: str) -> Path | None:
    root = Path(settings.evidence.store_path) / "incidents"
    candidate = (root / incident_id).resolve()
    if root.resolve() not in candidate.parents:
        return None
    if not candidate.is_dir():
        return None
    return candidate


def _incident_summary(package: Path) -> dict | None:
    metadata = _read_json(package / "incident_metadata.json")
    if not metadata:
        return None
    signature = _read_json(package / "signature.sha256")
    return {
        "incident_id": metadata.get("incident_id", package.name),
        "site_id": metadata.get("site_id"),
        "operator_id": metadata.get("operator_id"),
        "device_id": metadata.get("device_id"),
        "started_utc": metadata.get("started_utc"),
        "finalized": signature is not None,
        "signature": (signature or {}).get("signature"),
        "manifest_hash": (signature or {}).get("manifest_hash"),
    }


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _promote_reference(camera_id: int, preset_id: str, frame_bus) -> bool:
    """Write the candidate reference (or the live frame) to disk.

    Returns True if a reference was written.  Prefers the candidate from
    the most recent /api/snapshot call so the polygon coords match the
    image the operator drew on; falls back to the latest live frame so
    saves still work if no snapshot was captured first.
    """
    now = time.monotonic()
    frame: np.ndarray | None = None
    with _pending_lock:
        cached = _pending_refs.pop((camera_id, preset_id), None)
        # Garbage-collect stale stashes for other (cam, preset) pairs.
        stale = [k for k, v in _pending_refs.items() if now - v[0] > _PENDING_TTL_SECONDS]
        for k in stale:
            _pending_refs.pop(k, None)
    if cached and (now - cached[0]) <= _PENDING_TTL_SECONDS:
        frame = cached[1]
    else:
        snap = frame_bus.latest(camera_id)
        if snap is not None and snap.raw is not None:
            frame = snap.raw
    if frame is None:
        return False
    target = reference_path(_references_root(), camera_id, preset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        logger.warning("Could not encode reference for cam%d preset=%s", camera_id, preset_id)
        return False
    # Atomic write so the worker's mtime check never reads a half file.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(encoded.tobytes())
    tmp.replace(target)
    logger.info("Reference written: %s (%d bytes)", target, target.stat().st_size)
    return True


def _references_root() -> Path:
    return Path(settings.zones.runtime_path).parent / "references"
