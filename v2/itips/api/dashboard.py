"""Dashboard routes — cameras, workers, plates, snapshots, incidents.

Mounted on the public Flask app at port 5050. Read-and-write surface for
the operator UI. The frontend is plain HTML/JS served from
`itips/api/static/dashboard/`.

Authentication is intentionally absent at POC; the dashboard is bound to
the site LAN behind operator-managed access. Phase 1 adds the same
bearer/mTLS surface the inbound 8443 API already enforces.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, stream_with_context

from config.settings import settings
from itips.api.personnel_store import PersonnelStore
from itips.camera.dahua_face_db import DahuaFaceDBError
from itips.camera.dahua_health import run_for_all as run_health_for_all
from itips.camera.dahua_manager import DahuaManager
from itips.camera.dahua_plate_db import BLACK_LIST, RED_LIST, PlateListUnsupported
from itips.utils.clock import now_iso

logger = logging.getLogger(__name__)

_STATIC_ROOT = Path(__file__).resolve().parent / "static" / "dashboard"


def register_dashboard(
    app: Flask,
    *,
    frame_bus,
    dahua_manager: DahuaManager,
    personnel_store: PersonnelStore,
    alert_engine=None,
    event_tap=None,
    capability_router=None,
    face_engine=None,
    plate_engine=None,
    behavior_engine=None,
    zone_store=None,
    sensor_map=None,
    sensor_dispatcher=None,
    sensor_event_tap=None,
    axpro_listener=None,
    axpro_alertstream=None,
    axpro_admin=None,
    openai_validator=None,
    webhook_store=None,
    webhook_dispatcher=None,
    preset_state=None,
    camera_settings=None,
) -> None:
    """Wire all dashboard routes onto the given Flask app.

    The trailing optional services are the ML fallback layer (see
    `itips/ml/`). When supplied:
      * worker enrolment also writes face embeddings to the Jetson DB
      * `/api/health/capabilities` reports per-engine readiness
      * the health route refreshes the router on every probe
      * `/api/zones/<camera_id>` exposes CRUD for the behavioral
        polygon zones the BehaviorEngine evaluates each motion event.
    """

    @app.get("/dashboard")
    def dashboard_index():  # noqa: ANN202
        return send_from_directory(_STATIC_ROOT, "index.html")

    @app.get("/dashboard/<path:filename>")
    def dashboard_assets(filename: str):  # noqa: ANN202
        return send_from_directory(_STATIC_ROOT, filename)

    # JSON errors for /api/* — Flask's HTML 404/500 pages would break res.json().
    @app.errorhandler(404)
    def _api_404(err):  # noqa: ANN202
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "not found",
                            "path": request.path}), 404
        return err

    @app.errorhandler(405)
    def _api_405(err):  # noqa: ANN202
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "method not allowed",
                            "method": request.method,
                            "path": request.path}), 405
        return err

    @app.errorhandler(500)
    def _api_500(err):  # noqa: ANN202
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "server error",
                            "path": request.path,
                            "message": str(err)}), 500
        return err

    @app.get("/streams")
    def streams_index():  # noqa: ANN202
        """Stand-alone camera-wall page — just live feeds, no dashboard chrome."""
        return send_from_directory(_STATIC_ROOT, "streams.html")

    # ─── live MJPEG proxy ──────────────────────────────────────────
    # Streams the camera's native MJPEG substream straight through the
    # dashboard. Browser <img src="/live/1"> plays it directly — no
    # transcoding on our side, no Dahua credentials in the page.

    @app.get("/live/<int:camera_id>")
    def live_mjpeg(camera_id: int):  # noqa: ANN202
        client = dahua_manager.get(camera_id)
        if client is None:
            abort(404)
        channel = request.args.get("channel", default=1, type=int)
        # subtype=1 → substream (lower-res, gentler on the LAN + CPU);
        # subtype=0 → main stream (full quality). MJPEG availability
        # varies per camera model — many dual-sensor / 4MP+ Dahuas only
        # expose MJPEG on the substream (main stream is H.264 only).
        # We try the requested subtype first and fall back to the
        # substream if the camera 401/403/404s it. Saves operators from
        # having to know which subtype their model supports.
        requested = request.args.get("subtype", default=1, type=int)
        candidates = [requested]
        if requested != 1:
            candidates.append(1)

        upstream = None
        chosen_subtype = None
        last_status = None
        for try_subtype in candidates:
            try:
                resp = client.endpoint.get(
                    "/cgi-bin/mjpg/video.cgi",
                    params={"channel": channel, "subtype": try_subtype},
                    timeout=(1.5, 30),
                    stream=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cam %d: live proxy open failed (subtype=%d): %s",
                    camera_id, try_subtype, exc,
                )
                continue
            if resp.status_code == 200:
                upstream = resp
                chosen_subtype = try_subtype
                break
            last_status = resp.status_code
            resp.close()
            logger.info(
                "cam %d: subtype=%d returned HTTP %d, trying fallback",
                camera_id, try_subtype, resp.status_code,
            )

        if upstream is None:
            logger.warning(
                "cam %d: no MJPEG subtype available (last status %s)",
                camera_id, last_status,
            )
            abort(502)
        if chosen_subtype != requested:
            logger.info("cam %d: served subtype=%d as fallback for requested=%d",
                        camera_id, chosen_subtype, requested)

        # Mirror the camera's content-type so the browser parses the
        # multipart boundary correctly. If the camera omits it, fall back
        # to a generic mixed-replace mimetype.
        mimetype = upstream.headers.get(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=myboundary",
        )

        def gen():
            # Smaller chunks + no decode_unicode so we forward bytes ASAP —
            # browsers want the first JPEG frame within ~2s or they show
            # nothing. macOS Docker Desktop NAT can hold larger buffers.
            try:
                for chunk in upstream.iter_content(chunk_size=1024):
                    if chunk:
                        yield chunk
            except Exception:
                logger.exception("cam %d: live proxy stream errored", camera_id)
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

        return Response(
            stream_with_context(gen()),
            mimetype=mimetype,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ─── cameras ───────────────────────────────────────────────────

    @app.get("/api/cameras")
    def list_cameras():  # noqa: ANN202
        out = []
        for client in dahua_manager.all():
            out.append({
                "camera_id": client.camera_id,
                "endpoint": client.endpoint.safe_label(),
                "workers_group_id": client.workers_group_id,
                "ptz_connected": bool(client.ptz.is_connected),
            })
        return jsonify({"cameras": out})

    # ─── snapshots ─────────────────────────────────────────────────

    @app.get("/api/snapshot/<int:camera_id>")
    def snapshot(camera_id: int):  # noqa: ANN202
        snap = frame_bus.latest(camera_id)
        if snap is None or snap.raw is None:
            # Fall back to a fresh /cgi-bin/snapshot.cgi if the bus is empty
            # (cold start, no event has fired yet).
            client = dahua_manager.get(camera_id)
            if client is None:
                abort(404)
            frame = client.endpoint.snapshot(timeout=4.0)
            if frame is None:
                abort(503)
        else:
            frame = snap.raw
        import cv2
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            abort(500)
        return Response(
            encoded.tobytes(),
            mimetype="image/jpeg",
            headers={
                "X-Frame-Width": str(frame.shape[1]),
                "X-Frame-Height": str(frame.shape[0]),
                "Cache-Control": "no-store",
            },
        )

    # ─── workers (face DB) ─────────────────────────────────────────

    @app.get("/api/workers")
    def list_workers():  # noqa: ANN202
        # Pull Jetson EmbeddingStore once so each row gets `jetson_enrolled`.
        jetson_ids: set[str] = set()
        if face_engine is not None:
            try:
                jetson_ids = {r.person_id for r in face_engine._store.list_all()}  # noqa: SLF001
            except Exception:
                logger.exception("face_engine store read failed")
        return jsonify({
            "workers": [
                {
                    "person_id": rec.person_id,
                    "full_name": rec.full_name,
                    "cameras": rec.per_camera,
                    "jetson_enrolled": rec.person_id in jetson_ids,
                }
                for rec in personnel_store.list_all()
            ],
            "available_cameras": dahua_manager.camera_ids(),
            "jetson_available": face_engine is not None,
        })

    @app.get("/api/workers/<int:camera_id>")
    def list_workers_on_camera(camera_id: int):  # noqa: ANN202
        client = dahua_manager.get(camera_id)
        if client is None or not client.workers_group_id:
            abort(404)
        try:
            people = client.face_db.list_persons(group_id=client.workers_group_id)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502
        return jsonify({
            "camera_id": camera_id,
            "group_id": client.workers_group_id,
            "people": [asdict(p) for p in people],
        })

    @app.post("/api/workers")
    def add_worker():  # noqa: ANN202
        full_name = (request.form.get("full_name") or "").strip()
        person_id = (request.form.get("person_id") or "").strip()
        image = request.files.get("image")
        if not full_name or not image:
            return jsonify({"ok": False, "error": "full_name and image required"}), 400
        jpeg = image.read()
        if not jpeg:
            return jsonify({"ok": False, "error": "image is empty"}), 400
        # Derive a local person_id if the operator didn't provide one.
        if not person_id:
            from itips.utils.clock import now_utc
            person_id = f"local-{int(now_utc().timestamp())}"

        per_camera: dict[int, str] = {}
        failures: list[str] = []
        for client in dahua_manager.all():
            if not client.workers_group_id:
                failures.append(f"cam{client.camera_id}:no-group")
                continue
            try:
                uid = client.face_db.add_person(
                    group_id=client.workers_group_id,
                    name=full_name,
                    jpeg=jpeg,
                    sex=request.form.get("sex") or None,
                )
                per_camera[client.camera_id] = uid
            except (DahuaFaceDBError, Exception) as exc:  # noqa: BLE001
                failures.append(f"cam{client.camera_id}:{exc.__class__.__name__}")
                logger.warning("cam %d addPerson failed: %s", client.camera_id, exc)

        personnel_store.upsert(
            person_id=person_id, full_name=full_name, per_camera=per_camera,
        )

        # Mirror to Jetson face DB. Soft failure — missing engine /
        # extras / no-face image must not block the native-camera path.
        ml_enrolled = False
        ml_error: str | None = None
        if face_engine is not None:
            try:
                face_engine.enroll(
                    person_id=person_id,
                    full_name=full_name,
                    image_bytes=jpeg,
                )
                ml_enrolled = True
            except Exception as exc:  # noqa: BLE001
                ml_error = exc.__class__.__name__
                logger.warning("ml face enrol failed for %s: %s", person_id, exc)

        return jsonify({
            "ok": bool(per_camera) or ml_enrolled,
            "person_id": person_id,
            "cameras": per_camera,
            "ml_enrolled": ml_enrolled,
            "ml_error": ml_error,
            "failures": failures,
        })

    @app.delete("/api/workers/<person_id>")
    def delete_worker(person_id: str):  # noqa: ANN202
        record = personnel_store.get(person_id)
        if record is None:
            return jsonify({"ok": False, "error": "unknown person_id"}), 404
        cleared: list[int] = []
        failures: list[str] = []
        for cam_id, uid in record.per_camera.items():
            client = dahua_manager.get(cam_id)
            if client is None or not client.workers_group_id:
                failures.append(f"cam{cam_id}:not-available")
                continue
            try:
                client.face_db.delete_person(group_id=client.workers_group_id, uid=uid)
                cleared.append(cam_id)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"cam{cam_id}:{exc.__class__.__name__}")
        personnel_store.delete(person_id)
        if face_engine is not None:
            try:
                face_engine.remove(person_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ml face remove failed for %s: %s", person_id, exc)
        return jsonify({"ok": True, "cameras_cleared": cleared, "failures": failures})

    # ─── plate lists ───────────────────────────────────────────────

    @app.get("/api/plates")
    def list_plates():  # noqa: ANN202
        list_type = request.args.get("list_type", RED_LIST)
        if list_type not in (RED_LIST, BLACK_LIST):
            return jsonify({"ok": False, "error": "invalid list_type"}), 400
        # Per-camera response shape:
        #   {"supported": True,  "rows": [...]}             — happy path
        #   {"supported": False, "reason": "..."}           — camera has no ANPR
        #   {"supported": True,  "error": "...", "rows": []} — transient failure
        out: dict[int, dict[str, Any]] = {}
        for client in dahua_manager.all():
            try:
                rows = client.plate_db.list(list_type=list_type, limit=200)
                out[client.camera_id] = {"supported": True, "rows": [asdict(r) for r in rows]}
            except PlateListUnsupported as exc:
                out[client.camera_id] = {
                    "supported": False,
                    "reason": "ANPR not enabled on this camera",
                    "detail": str(exc),
                }
            except Exception as exc:  # noqa: BLE001
                out[client.camera_id] = {
                    "supported": True,
                    "rows": [],
                    "error": exc.__class__.__name__,
                }
        return jsonify({"list_type": list_type, "cameras": out})

    @app.post("/api/plates")
    def add_plate():  # noqa: ANN202
        body = request.get_json(silent=True) or {}
        list_type = body.get("list_type", RED_LIST)
        plate_number = (body.get("plate_number") or "").strip().upper()
        if not plate_number:
            return jsonify({"ok": False, "error": "plate_number required"}), 400
        if list_type not in (RED_LIST, BLACK_LIST):
            return jsonify({"ok": False, "error": "invalid list_type"}), 400
        target_cameras = body.get("cameras") or dahua_manager.camera_ids()
        results: dict[int, Any] = {}
        for cam_id in target_cameras:
            client = dahua_manager.get(int(cam_id))
            if client is None:
                results[int(cam_id)] = {"error": "unknown camera"}
                continue
            try:
                rec_no = client.plate_db.add(
                    list_type=list_type,
                    plate_number=plate_number,
                    master_of_car=body.get("master_of_car"),
                    plate_color=body.get("plate_color"),
                    vehicle_color=body.get("vehicle_color"),
                    open_gate=bool(body.get("open_gate", False)),
                )
                results[int(cam_id)] = {"rec_no": rec_no}
            except Exception as exc:  # noqa: BLE001
                results[int(cam_id)] = {"error": str(exc)}
        return jsonify({"ok": True, "list_type": list_type, "plate": plate_number, "cameras": results})

    @app.delete("/api/plates/<int:camera_id>/<int:rec_no>")
    def delete_plate(camera_id: int, rec_no: int):  # noqa: ANN202
        list_type = request.args.get("list_type", RED_LIST)
        if list_type not in (RED_LIST, BLACK_LIST):
            return jsonify({"ok": False, "error": "invalid list_type"}), 400
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        try:
            client.plate_db.remove(list_type=list_type, rec_no=rec_no)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True})

    # ─── deterrence test (operator-only) ───────────────────────────

    @app.post("/api/deterrence/<int:camera_id>/fire")
    def fire_deterrence(camera_id: int):  # noqa: ANN202
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        body = request.get_json(silent=True) or {}
        try:
            client.deterrence.fire(
                light=bool(body.get("light", True)),
                speaker=bool(body.get("speaker", True)),
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True})

    @app.post("/api/deterrence/<int:camera_id>/standdown")
    def standdown_deterrence(camera_id: int):  # noqa: ANN202
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        try:
            client.deterrence.standdown()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True})

    # ─── incidents ─────────────────────────────────────────────────

    @app.post("/api/evidence/test/run")
    def evidence_test_run():  # noqa: ANN202
        """Build a complete test package end-to-end. Real packager,
        real signature, real PDF — just synthetic JPEG+event payloads.
        Returns the package dir + signature so the operator can jump
        straight to inspecting it in the Incidents tab."""
        import cv2
        import numpy as np
        packager = getattr(alert_engine, "_packager", None)
        if packager is None:
            return jsonify({"ok": False, "error": "no packager wired"}), 503
        incident_id = packager.start_incident(
            site_id=settings.tenant.site_id or "test-site",
            operator_id=settings.tenant.operator_id or "test-operator",
            device_id=settings.tenant.device_id or "test-device",
        )
        # Synthesise tiny but valid JPEGs so the package contains real
        # face + plate evidence files.
        fake = np.zeros((120, 160, 3), dtype="uint8")
        cv2.putText(fake, "TEST", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (255, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", fake)
        jpeg = buf.tobytes() if ok else b""
        packager.attach_face_capture(incident_id, jpeg=jpeg,
                                       confidence=0.78, name="TEST_PERSON")
        packager.attach_plate_capture(incident_id, jpeg=jpeg,
                                        plate_number="LAG-T35-XY",
                                        confidence=0.81)
        packager.attach_event(incident_id, {
            "kind": "stage_change", "stage": "PRELIMINARY",
            "signal": "first_event", "camera_id": 4,
            "timestamp_utc": now_iso(),
        })
        packager.attach_event(incident_id, {
            "kind": "sensor_alarm", "camera_id": 4, "zone_id": 1,
            "sensor_type": "doorContact", "timestamp_utc": now_iso(),
        })
        packager.attach_event(incident_id, {
            "kind": "stage_change", "stage": "CONFIRMED",
            "signal": "face_intruder", "camera_id": 4,
            "timestamp_utc": now_iso(),
        })
        packager.update_metadata(incident_id, {
            "incident_classification": "perimeter_breach",
            "gps_coordinates": [
                float(settings.tenant.latitude or 0.0),
                float(settings.tenant.longitude or 0.0),
            ],
            "responder_dispatch_log": [],
        })
        try:
            package_dir = packager.finalize(incident_id,
                                              closed_reason="dashboard_test",
                                              timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": exc.__class__.__name__,
                            "message": str(exc)}), 502
        sig_path = Path(package_dir) / "signature.sha256"
        sig = _read_json(sig_path) or {}
        return jsonify({
            "ok": True,
            "incident_id": incident_id,
            "package_dir": str(package_dir),
            "signature": sig.get("signature"),
            "manifest_hash": sig.get("manifest_hash"),
        })

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
        meta = _read_json(package / "incident_metadata.json")
        manifest = _read_json(package / "manifest.json")
        sig = _read_json(package / "signature.sha256")

        def _list_subdir(name: str) -> list[dict]:
            sub = package / name
            if not sub.is_dir():
                return []
            return [{"filename": f"{name}/{f.name}",
                     "bytes": f.stat().st_size}
                    for f in sorted(sub.iterdir()) if f.is_file()]

        # Index manifest entries by kind for quick UI lookups.
        manifest_files = (manifest or {}).get("files", []) or []
        kinds = {entry["kind"] for entry in manifest_files if "kind" in entry}
        has_pdf = any(e.get("kind") == "summary_pdf" for e in manifest_files)
        has_highlight = any(e.get("kind") == "video_highlight" for e in manifest_files)
        return jsonify({
            "incident_id": incident_id,
            "metadata": meta,
            "manifest": manifest,
            "signature": sig,
            "files": [f.relative_to(package).as_posix()
                      for f in sorted(package.rglob("*")) if f.is_file()],
            "face_captures": _list_subdir("face_captures"),
            "plate_captures": _list_subdir("plate_captures"),
            "video_files": [e["filename"] for e in manifest_files
                            if e.get("kind", "").startswith("video_")],
            "has_pdf": has_pdf,
            "has_highlight": has_highlight,
            "manifest_kinds": sorted(kinds),
        })

    @app.post("/api/incidents/<incident_id>/verify")
    def verify_incident(incident_id: str):  # noqa: ANN202
        """Recompute every file's SHA-256 against the manifest, then
        recompute the manifest hash. Surfaces tampering with file-level
        granularity — UI can show a green/red badge."""
        package = _safe_incident_dir(incident_id)
        if package is None:
            return jsonify({"ok": False, "error": "incident not found"}), 404
        manifest = _read_json(package / "manifest.json")
        if not manifest:
            return jsonify({"ok": False, "error": "manifest missing"}), 400
        from itips.evidence.signing import compute_file_hash
        file_checks = []
        all_ok = True
        for entry in manifest.get("files", []):
            fp = package / entry["filename"]
            if not fp.exists():
                file_checks.append({"filename": entry["filename"],
                                    "status": "missing"})
                all_ok = False
                continue
            actual = compute_file_hash(fp)
            ok = actual == entry["sha256"]
            file_checks.append({
                "filename": entry["filename"],
                "status": "ok" if ok else "mismatch",
                "expected": entry["sha256"],
                "actual": actual if not ok else None,
            })
            if not ok:
                all_ok = False
        # Recompute manifest hash to confirm signature integrity end-to-end.
        import hashlib
        manifest_body = (package / "manifest.json").read_bytes()
        actual_manifest_hash = hashlib.sha256(manifest_body).hexdigest()
        sig = _read_json(package / "signature.sha256") or {}
        signed_hash = sig.get("manifest_hash")
        manifest_ok = signed_hash == actual_manifest_hash
        return jsonify({
            "ok": all_ok and manifest_ok,
            "manifest_hash_signed": signed_hash,
            "manifest_hash_actual": actual_manifest_hash,
            "manifest_hash_ok": manifest_ok,
            "files": file_checks,
        })

    @app.get("/api/incidents/<incident_id>/files/<path:filename>")
    def download_incident_file(incident_id: str, filename: str):  # noqa: ANN202
        """`?inline=1` for inline render (thumbnails / PDF preview)."""
        package = _safe_incident_dir(incident_id)
        if package is None:
            abort(404)
        candidate = (package / filename).resolve()
        if not candidate.is_file() or package not in candidate.parents:
            abort(404)
        inline = _truthy(request.args.get("inline"))
        return send_file(candidate, as_attachment=not inline)

    # ─── health checks ─────────────────────────────────────────────
    # Per-camera capability matrix. Caches a result briefly so a click-happy
    # operator doesn't re-probe every camera multiple times per second.
    health_cache = {"timestamp": 0.0, "body": None}
    _HEALTH_CACHE_TTL = 20.0  # seconds

    @app.get("/api/health/cameras")
    def get_camera_health():  # noqa: ANN202
        force = request.args.get("force", "").lower() in {"1", "true", "yes"}
        now = time.monotonic()
        if (not force
                and health_cache["body"] is not None
                and (now - health_cache["timestamp"]) < _HEALTH_CACHE_TTL):
            cached = dict(health_cache["body"])
            cached["cached"] = True
            return jsonify(cached)
        body = run_health_for_all(dahua_manager, event_tap)
        body["cached"] = False
        # Keep the capability router in lockstep with the probe matrix
        # so the event worker's needs_fallback() answers reflect what
        # the operator just looked at on the dashboard.
        if capability_router is not None:
            try:
                capability_router.update_from_health(body)
            except Exception:
                logger.exception("capability router refresh failed")
        health_cache["timestamp"] = now
        health_cache["body"] = body
        return jsonify(body)

    @app.get("/api/health/capabilities")
    def get_capability_summary():  # noqa: ANN202
        """Per-camera capability vector + engine readiness + overrides."""
        if capability_router is None:
            return jsonify({"available": False, "cameras": {}})
        face_ready = bool(face_engine and face_engine.is_ready()) if face_engine else False
        plate_ready = bool(plate_engine and plate_engine.is_ready()) if plate_engine else False
        behavior_ready = bool(behavior_engine and behavior_engine.is_ready()) if behavior_engine else False
        return jsonify({
            "available": True,
            "face_engine_ready": face_ready,
            "plate_engine_ready": plate_ready,
            "behavior_engine_ready": behavior_ready,
            "cameras": capability_router.summary(),
            "overrides": capability_router.overrides(),
        })

    @app.post("/api/health/capabilities/<int:camera_id>/<cap>/override")
    def set_capability_override(camera_id: int, cap: str):  # noqa: ANN202
        """Body: `{"force_fallback": true|false|null}` — null clears."""
        if capability_router is None:
            return jsonify({"ok": False, "error": "capability router not wired"}), 503
        try:
            from itips.ml.capability_router import Capability
            cap_enum = Capability(cap)
        except (ImportError, ValueError):
            return jsonify({"ok": False, "error": f"unknown capability {cap!r}"}), 400
        body = request.get_json(silent=True) or {}
        if "force_fallback" not in body:
            return jsonify({"ok": False,
                            "error": "force_fallback (true|false|null) required"}), 400
        force = body["force_fallback"]
        if force is not None and not isinstance(force, bool):
            return jsonify({"ok": False,
                            "error": "force_fallback must be boolean or null"}), 400
        capability_router.set_override(camera_id, cap_enum, force)
        return jsonify({
            "ok": True,
            "camera_id": camera_id,
            "capability": cap,
            "force_fallback": force,
            "effective_needs_fallback": capability_router.needs_fallback(camera_id, cap_enum),
        })

    # ─── ML Lab — direct engine invocation ─────────────────────────
    # `?dispatch=1` also routes the result through the AlertEngine.

    @app.get("/api/ml/status")
    def ml_status():  # noqa: ANN202
        return jsonify({
            "face":     _engine_status(face_engine),
            "plate":    _engine_status(plate_engine),
            "behavior": _engine_status(behavior_engine),
        })

    @app.post("/api/ml/warmup")
    def ml_warmup_all():  # noqa: ANN202
        for eng in (face_engine, plate_engine, behavior_engine):
            if eng is not None:
                try:
                    eng.warmup_async()
                except Exception:
                    logger.exception("ml warmup_async failed")
        return jsonify({
            "face":     _engine_status(face_engine),
            "plate":    _engine_status(plate_engine),
            "behavior": _engine_status(behavior_engine),
        })

    @app.get("/api/ml/face/enrolled")
    def ml_face_enrolled():  # noqa: ANN202
        if face_engine is None:
            return jsonify({"available": False, "people": []})
        try:
            records = face_engine._store.list_all()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            return jsonify({"available": True, "error": str(exc), "people": []}), 502
        return jsonify({
            "available": True,
            "count": len(records),
            "people": [
                {"person_id": r.person_id, "full_name": r.full_name, "dim": r.dim}
                for r in records
            ],
        })

    @app.post("/api/ml/face/recognize")
    def ml_face_recognize():  # noqa: ANN202
        if face_engine is None:
            return jsonify({"ok": False, "error": "face engine not wired"}), 503
        frame = _decode_upload(request)
        if frame is None:
            return jsonify({"ok": False, "error": "no image provided"}), 400
        try:
            result = face_engine.recognize(frame, bbox=None)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": exc.__class__.__name__,
                            "message": str(exc)}), 502
        dispatched = False
        if alert_engine is not None and _truthy(request.args.get("dispatch")):
            camera_id = int(request.form.get("camera_id", 0) or 0)
            if result.matched and result.person_id:
                alert_engine.handle_personnel_seen(
                    camera_id=camera_id,
                    person_uid=result.person_id,
                    group_id="jetson-fallback",
                    name=result.full_name or "",
                    similarity=int(round(result.similarity * 100)),
                )
            else:
                alert_engine.handle_face_intruder(
                    camera_id=camera_id, face_bbox=(0, 0, 0, 0), name="INTRUDER",
                )
            dispatched = True
        return jsonify({
            "ok": True,
            "engine_ready": face_engine.is_ready(),
            "matched": result.matched,
            "person_id": result.person_id,
            "full_name": result.full_name,
            "similarity": round(float(result.similarity), 4),
            "dispatched": dispatched,
        })

    @app.post("/api/ml/plate/read")
    def ml_plate_read():  # noqa: ANN202
        if plate_engine is None:
            return jsonify({"ok": False, "error": "plate engine not wired"}), 503
        frame = _decode_upload(request)
        if frame is None:
            return jsonify({"ok": False, "error": "no image provided"}), 400
        try:
            result = plate_engine.read_plate(frame)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": exc.__class__.__name__,
                            "message": str(exc)}), 502
        dispatched = False
        if result is not None and alert_engine is not None and _truthy(request.args.get("dispatch")):
            camera_id = int(request.form.get("camera_id", 0) or 0)
            alert_engine.handle_plate_capture(
                camera_id=camera_id,
                plate_number=result.plate_number,
                plate_color=None, vehicle_color=None, speed=None,
            )
            dispatched = True
        return jsonify({
            "ok": True,
            "engine_ready": plate_engine.is_ready(),
            "found": result is not None,
            "plate_number": result.plate_number if result else None,
            "confidence": round(float(result.confidence), 4) if result else None,
            "bbox": list(result.bbox) if result else None,
            "raw_text": result.raw_text if result else None,
            "dispatched": dispatched,
        })

    @app.get("/api/ml/openai/status")
    def ml_openai_status():  # noqa: ANN202
        if openai_validator is None:
            return jsonify({"wired": False,
                            "reason": "ITIPS_OPENAI_ENABLED not set"})
        used, cap = openai_validator.hourly_token_usage()
        return jsonify({
            "wired": True,
            "enabled": openai_validator.is_enabled(),
            "configured": openai_validator.is_configured(),
            "model": openai_validator.default_model,
            "scenarios": openai_validator.scenarios,
            "tokens_used_hour": used,
            "tokens_cap_hour": cap,
            "recent": openai_validator.recent(limit=20),
        })

    @app.post("/api/ml/openai/validate/<scenario>")
    def ml_openai_validate(scenario: str):  # noqa: ANN202
        """Direct probe — bypass should_validate so operators can test any
        prompt regardless of confidence band or cooldown. Token quota
        still applies (the kill-switch matters even for testing)."""
        if openai_validator is None or not openai_validator.is_enabled():
            return jsonify({"ok": False, "error": "openai validator not enabled"}), 503
        if scenario not in openai_validator.scenarios:
            return jsonify({"ok": False, "error": f"unknown scenario {scenario!r}",
                            "known": openai_validator.scenarios}), 400
        frame = _decode_upload(request)
        if frame is None:
            return jsonify({"ok": False, "error": "no image provided"}), 400
        context = {k: v for k, v in request.form.items() if k != "image"}
        try:
            result = openai_validator.validate(scenario, frame, context)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": exc.__class__.__name__,
                            "message": str(exc)}), 502
        if result is None:
            return jsonify({"ok": False, "error": "validator returned None "
                            "(quota exhausted or API error — check logs)"}), 502
        return jsonify({
            "ok": True,
            "scenario": result.scenario,
            "verdict": result.verdict,
            "category": result.category,
            "confidence": result.confidence,
            "summary": result.summary,
            "model": result.model,
            "tokens_used": result.tokens_used,
            "should_suppress": result.should_suppress,
            "should_escalate": result.should_escalate,
        })

    # ─── outbound webhooks ─────────────────────────────────────────
    # Subscriber CRUD for the push-event system. Consumers register a
    # URL + secret + event filter and receive HMAC-signed POSTs as
    # alerts/validator-verdicts/sensor events happen, instead of polling
    # /api/alerts/latest. See docs/WEBHOOKS.md for the payload schema.

    @app.get("/api/webhooks/event-kinds")
    def webhooks_event_kinds():  # noqa: ANN202
        from itips.webhooks.events import EVENT_KINDS
        return jsonify({
            "ok": True,
            "kinds": [{"name": k, "description": v}
                      for k, v in EVENT_KINDS.items()],
        })

    @app.get("/api/webhooks/subscribers")
    def webhooks_list():  # noqa: ANN202
        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        return jsonify({
            "ok": True,
            "subscribers": [s.to_public() for s in webhook_store.list()],
        })

    @app.post("/api/webhooks/subscribers")
    def webhooks_create():  # noqa: ANN202
        from itips.webhooks.events import EVENT_KINDS
        from itips.webhooks.signing import generate_secret

        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        body = request.get_json(silent=True) or {}
        url = str(body.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return jsonify({"ok": False,
                            "error": "url must start with http:// or https://"}), 400
        raw_filter = body.get("event_filter") or ["*"]
        if not isinstance(raw_filter, list) or not raw_filter:
            return jsonify({"ok": False,
                            "error": "event_filter must be a non-empty list"}), 400
        # Validate each requested kind so subscribers can't register
        # for a kind that will never fire (silent misconfiguration).
        unknown = [k for k in raw_filter
                   if k != "*" and k not in EVENT_KINDS]
        if unknown:
            return jsonify({
                "ok": False,
                "error": "unknown event kind(s)",
                "unknown": unknown,
                "known": sorted(EVENT_KINDS.keys()),
            }), 400
        secret = (str(body.get("secret") or "").strip()
                  or generate_secret())
        description = str(body.get("description") or "").strip()
        enabled = bool(body.get("enabled", True))
        sub = webhook_store.create(
            url=url, secret=secret, event_filter=raw_filter,
            description=description, enabled=enabled,
        )
        # The secret is returned ONCE here so the operator can copy it
        # into the consumer service. Subsequent GETs omit it.
        return jsonify({"ok": True, "subscriber": sub.to_public(include_secret=True)}), 201

    @app.get("/api/webhooks/subscribers/<sub_id>")
    def webhooks_get(sub_id: str):  # noqa: ANN202
        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        sub = webhook_store.get(sub_id)
        if sub is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "subscriber": sub.to_public()})

    @app.patch("/api/webhooks/subscribers/<sub_id>")
    def webhooks_patch(sub_id: str):  # noqa: ANN202
        from itips.webhooks.events import EVENT_KINDS

        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        body = request.get_json(silent=True) or {}
        patch = {}
        if "url" in body:
            url = str(body["url"] or "").strip()
            if not url.startswith(("http://", "https://")):
                return jsonify({"ok": False,
                                "error": "url must start with http:// or https://"}), 400
            patch["url"] = url
        if "event_filter" in body:
            filt = body["event_filter"]
            if not isinstance(filt, list) or not filt:
                return jsonify({"ok": False,
                                "error": "event_filter must be a non-empty list"}), 400
            unknown = [k for k in filt if k != "*" and k not in EVENT_KINDS]
            if unknown:
                return jsonify({"ok": False, "error": "unknown event kind(s)",
                                "unknown": unknown}), 400
            patch["event_filter"] = filt
        if "secret" in body and body["secret"]:
            patch["secret"] = str(body["secret"]).strip()
        if "description" in body:
            patch["description"] = str(body["description"] or "")
        if "enabled" in body:
            patch["enabled"] = bool(body["enabled"])
        sub = webhook_store.update(sub_id, **patch)
        if sub is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "subscriber": sub.to_public()})

    @app.delete("/api/webhooks/subscribers/<sub_id>")
    def webhooks_delete(sub_id: str):  # noqa: ANN202
        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        removed = webhook_store.delete(sub_id)
        return jsonify({"ok": True, "removed": removed})

    @app.post("/api/webhooks/subscribers/<sub_id>/test")
    def webhooks_test(sub_id: str):  # noqa: ANN202
        if webhook_dispatcher is None:
            return jsonify({"ok": False, "error": "webhooks dispatcher not running"}), 503
        sent = webhook_dispatcher.dispatch_test(sub_id, tenant=settings.tenant)
        if sent == 0:
            return jsonify({"ok": False,
                            "error": "subscriber not found or disabled"}), 404
        return jsonify({"ok": True, "queued": sent})

    @app.post("/api/webhooks/subscribers/<sub_id>/rotate-secret")
    def webhooks_rotate(sub_id: str):  # noqa: ANN202
        from itips.webhooks.signing import generate_secret
        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        new_secret = generate_secret()
        sub = webhook_store.update(sub_id, secret=new_secret)
        if sub is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True,
                        "subscriber": sub.to_public(include_secret=True)})

    @app.get("/api/webhooks/deliveries")
    def webhooks_deliveries():  # noqa: ANN202
        if webhook_store is None:
            return jsonify({"ok": False, "error": "webhooks not wired"}), 503
        sub_id = request.args.get("subscriber_id") or None
        limit = request.args.get("limit", default=50, type=int)
        return jsonify({
            "ok": True,
            "deliveries": webhook_store.list_deliveries(
                subscriber_id=sub_id, limit=limit,
            ),
        })

    @app.post("/api/ml/behavior/<int:camera_id>/analyse")
    def ml_behavior_analyse(camera_id: int):  # noqa: ANN202
        if behavior_engine is None:
            return jsonify({"ok": False, "error": "behavior engine not wired"}), 503
        frame = _decode_upload(request)
        if frame is None:
            return jsonify({"ok": False, "error": "no image provided"}), 400
        try:
            analysis = behavior_engine.analyse_full(camera_id, frame)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": exc.__class__.__name__,
                            "message": str(exc)}), 502
        dispatched = 0
        if alert_engine is not None and _truthy(request.args.get("dispatch")):
            for a in analysis.alerts:
                alert_engine.handle_behaviour_alert_simple(
                    camera_id=camera_id,
                    alert_type=a.alert_type,
                    details={**a.details,
                             "zone_id": a.zone_id, "track_id": a.track_id,
                             "class_name": a.class_name, "bbox": list(a.bbox)},
                )
                dispatched += 1
        zones = zone_store.for_camera(camera_id) if zone_store else []
        h, w = frame.shape[:2]
        return jsonify({
            "ok": True,
            "engine_ready": behavior_engine.is_ready(),
            "camera_id": camera_id,
            "frame_size": [w, h],
            "zone_count": len(zones),
            "zones": [_zone_to_dict(z) for z in zones],
            "detections": [_detection_to_dict(d, w, h) for d in analysis.detections],
            "alerts": [_behavior_alert_to_dict(a) for a in analysis.alerts],
            "dispatched": dispatched,
        })

    # ─── zone CRUD for the behavioral fallback ─────────────────────
    # Polygon coords are stored normalised to `[0, 1]` in image space
    # so the same zone keeps working across stream resolutions and
    # snapshot resizes.

    @app.get("/api/zones/<int:camera_id>")
    def list_zones(camera_id: int):  # noqa: ANN202
        if zone_store is None:
            return jsonify({"available": False, "zones": []})
        zones = [_zone_to_dict(z) for z in zone_store.for_camera(camera_id)]
        return jsonify({"available": True, "camera_id": camera_id, "zones": zones})

    @app.post("/api/zones/<int:camera_id>")
    def upsert_zone(camera_id: int):  # noqa: ANN202
        if zone_store is None:
            return jsonify({"ok": False, "error": "zone store not wired"}), 503
        body = request.get_json(silent=True) or {}
        try:
            zone = _zone_from_dict(body)
        except (ValueError, KeyError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        zone_store.upsert_zone(camera_id, zone)
        return jsonify({"ok": True, "zone": _zone_to_dict(zone)})

    @app.delete("/api/zones/<int:camera_id>/<zone_id>")
    def delete_zone(camera_id: int, zone_id: str):  # noqa: ANN202
        if zone_store is None:
            return jsonify({"ok": False, "error": "zone store not wired"}), 503
        removed = zone_store.remove_zone(camera_id, zone_id)
        if not removed:
            return jsonify({"ok": False, "error": "zone not found"}), 404
        return jsonify({"ok": True})

    # ─── Sensors — AX PRO zone → PTZ preset map + test triggers ───

    @app.get("/api/sensors/map")
    def list_sensor_mappings():  # noqa: ANN202
        if sensor_map is None:
            return jsonify({"available": False, "mappings": []})
        return jsonify({
            "available": True,
            "mappings": [_sensor_mapping_to_dict(m) for m in sensor_map.all()],
        })

    @app.post("/api/sensors/map")
    def upsert_sensor_mapping():  # noqa: ANN202
        if sensor_map is None:
            return jsonify({"ok": False, "error": "sensor map not wired"}), 503
        body = request.get_json(silent=True) or {}
        try:
            from itips.sensors.sensor_map import SensorMapping
            mapping = SensorMapping(
                zone_id=int(body["zone_id"]),
                camera_id=int(body["camera_id"]),
                preset_name=str(body["preset_name"]),
                sensor_type=str(body.get("sensor_type", "")),
                description=str(body.get("description", "")),
                metadata=dict(body.get("metadata") or {}),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        sensor_map.upsert(mapping)
        return jsonify({"ok": True, "mapping": _sensor_mapping_to_dict(mapping)})

    @app.delete("/api/sensors/map/<int:zone_id>")
    def delete_sensor_mapping(zone_id: int):  # noqa: ANN202
        if sensor_map is None:
            return jsonify({"ok": False, "error": "sensor map not wired"}), 503
        if not sensor_map.remove(zone_id):
            return jsonify({"ok": False, "error": "zone not found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/sensors/simulate/<int:zone_id>")
    def simulate_sensor(zone_id: int):  # noqa: ANN202
        """Inject a synthetic sensor event — same pipeline as a real hub event."""
        if sensor_dispatcher is None:
            return jsonify({"ok": False, "error": "sensor dispatcher not wired"}), 503
        from itips.sensors.sensor_event import SensorEvent
        body = request.get_json(silent=True) or {}
        event = SensorEvent(
            zone_id=zone_id,
            event_type=str(body.get("event_type", "doorContact")),
            event_state=str(body.get("event_state", "alarm")),
            zone_name=str(body.get("zone_name", f"sim-zone-{zone_id}")),
            source="simulate",
        )
        accepted = sensor_dispatcher.dispatch(event)
        if not accepted:
            return jsonify({"ok": False, "error": "queue full"}), 503
        return jsonify({"ok": True, "queued": True, "event": event.to_dict()})

    @app.get("/api/sensors/events/recent")
    def recent_sensor_events():  # noqa: ANN202
        if sensor_event_tap is None:
            return jsonify({"available": False, "events": []})
        limit = request.args.get("limit", type=int, default=50)
        return jsonify({
            "available": True,
            "events": sensor_event_tap.recent(limit=limit),
        })

    @app.delete("/api/sensors/events/recent")
    def clear_sensor_events():  # noqa: ANN202
        if sensor_event_tap is None:
            return jsonify({"ok": False, "error": "tap not wired"}), 503
        sensor_event_tap.clear()
        return jsonify({"ok": True})

    @app.get("/api/sensors/listener/status")
    def axpro_listener_status():  # noqa: ANN202
        """AX PRO hub listener health — drives the pill in the Sensors tab."""
        if axpro_listener is None:
            return jsonify({
                "wired": False,
                "reason": "ITIPS_AXPRO_HOST not set — use Simulate to test",
            })
        out = {
            "wired": True,
            "host": axpro_listener.host,
            "connected": bool(axpro_listener.is_connected),
            "armed": bool(axpro_listener.is_armed),
            "last_error": axpro_listener.last_error,
            "thread_alive": axpro_listener.is_alive(),
        }
        # Include alertStream subscription state alongside the poller's
        # so the dashboard can show both channels at a glance.
        if axpro_alertstream is not None:
            out["alertstream"] = {
                "connected": bool(axpro_alertstream.is_connected),
                "events_received": axpro_alertstream.events_received,
                "last_error": axpro_alertstream.last_error,
                "thread_alive": axpro_alertstream.is_alive(),
            }
        return jsonify(out)

    @app.get("/api/sensors/listener/zones")
    def axpro_listener_zones():  # noqa: ANN202
        """Diagnostic dump — raw hub zone payload plus the listener's
        cached edge state. Use this when a real sensor activates and
        nothing reaches the events feed: compare `raw.ZoneList[...]`
        against `edge_state` to see whether the field name we read
        matches what the hub is actually publishing on this firmware."""
        if axpro_listener is None:
            return jsonify({"wired": False,
                            "reason": "ITIPS_AXPRO_HOST not set"})
        return jsonify({
            "wired": True,
            "connected": bool(axpro_listener.is_connected),
            "edge_state": axpro_listener.alarm_state_snapshot(),
            "raw": axpro_listener.fetch_raw_zone_status(),
        })

    # ─── AX PRO hub control ───────────────────────────────────────
    # These routes let an operator drive arming and bypass state from
    # the dashboard without climbing into the Hik-Connect mobile app.
    # The hub has three firmware quirks we paper over here:
    #   * `arm_away` fails with `armedStatus` if the subsystem is
    #     already armed — we always disarm first.
    #   * `bypassRecover` is the live ISAPI path; the older
    #     `Recoverbypass/` returns 404 on current firmware. The admin
    #     helper uses the working path.
    #   * Bypass changes are rejected while the parent subsystem is
    #     armed. The arm-away route accepts ?clear_bypass=<zone_id>
    #     and clears those during the disarmed window.

    def _admin_or_503():
        if axpro_admin is None:
            return jsonify({"ok": False,
                            "error": "axpro admin not wired — ITIPS_AXPRO_HOST not set"}), 503
        return None

    @app.get("/api/sensors/hub/state")
    def hub_state():  # noqa: ANN202
        """Full subsystem + zone snapshot from the hub. Backs the
        dashboard's hub-admin panel — exposes arming mode per
        subsystem, bypass state per zone, and the per-zone
        armNoBypassEnabled flag so operators can see at a glance why
        a sensor isn't firing."""
        err = _admin_or_503()
        if err is not None:
            return err
        client = axpro_admin._require_client()  # noqa: SLF001
        try:
            subs = (client.subsystem_status() or {}).get("SubSysList", [])
            zones = (client.zone_status() or {}).get("ZoneList", [])
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({
            "ok": True,
            "subsystems": [s.get("SubSys", {}) for s in subs],
            "zones": [z.get("Zone", {}) for z in zones],
        })

    @app.post("/api/sensors/hub/disarm")
    def hub_disarm():  # noqa: ANN202
        """Disarm one or more subsystems. POST {"sub_ids": [1, 2, 3]}."""
        err = _admin_or_503()
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        sub_ids = [int(x) for x in (body.get("sub_ids") or [1, 2, 3])]
        return jsonify({"ok": True, "results": axpro_admin.disarm_all(sub_ids)})

    @app.post("/api/sensors/hub/arm-away")
    def hub_arm_away():  # noqa: ANN202
        """Arm subsystems Away. Always disarm-first to dodge the
        `armedStatus` rejection. POST body:
            {"sub_ids": [1,2,3], "clear_bypass": [1]}
        The optional `clear_bypass` list is processed during the
        disarmed window — useful when a zone needs an un-bypass *and*
        an arm in one operator action."""
        err = _admin_or_503()
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        sub_ids = [int(x) for x in (body.get("sub_ids") or [1, 2, 3])]
        clear_bypass = [int(z) for z in (body.get("clear_bypass") or [])]

        disarm_results = axpro_admin.disarm_all(sub_ids)
        bypass_results = []
        for zid in clear_bypass:
            try:
                bypass_results.append({"zone_id": zid, "ok": True,
                                       "payload": axpro_admin.unbypass_zone(zid)})
            except Exception as exc:  # noqa: BLE001
                bypass_results.append({"zone_id": zid, "ok": False, "error": str(exc)})
        arm_results = axpro_admin.arm_away_all(sub_ids, disarm_first=False)
        return jsonify({
            "ok": all(r.get("ok", True) for r in arm_results),
            "disarm": disarm_results,
            "bypass_recover": bypass_results,
            "arm_away": arm_results,
        })

    @app.post("/api/sensors/zones/<int:zone_id>/unbypass")
    def hub_unbypass(zone_id: int):  # noqa: ANN202
        """Clear an operational bypass on `zone_id`. May 502 with
        `armedStatus` if the parent subsystem is currently armed; the
        dashboard should fall back to /hub/arm-away with
        clear_bypass=[zone_id] in that case."""
        err = _admin_or_503()
        if err is not None:
            return err
        try:
            payload = axpro_admin.unbypass_zone(zone_id)
            return jsonify({"ok": True, "payload": payload})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc),
                            "payload": getattr(exc, "payload", None)}), 502

    @app.post("/api/sensors/zones/<int:zone_id>/bypass")
    def hub_bypass(zone_id: int):  # noqa: ANN202
        err = _admin_or_503()
        if err is not None:
            return err
        try:
            payload = axpro_admin.bypass_zone(zone_id)
            return jsonify({"ok": True, "payload": payload})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc),
                            "payload": getattr(exc, "payload", None)}), 502

    # ─── siren (panic alarm) ──────────────────────────────────────
    # Manual control of the hub's siren (Hikvision "One-Key Alarm").
    # PUT to `oneKeyAlarm/{sub_id}` triggers every siren bound to that
    # subsystem; `clearAlarm/{sub_id}` silences them. We surface this
    # at /api/sensors/hub/siren/{action} so the dashboard button can
    # call it without knowing the ISAPI shape.

    @app.get("/api/sensors/hub/siren/status")
    def hub_siren_status():  # noqa: ANN202
        """Per-siren status (online/offline, signal, battery, tamper)."""
        err = _admin_or_503()
        if err is not None:
            return err
        try:
            return jsonify({"ok": True, "sirens": axpro_admin.siren_status()})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502

    @app.post("/api/sensors/hub/siren/start")
    def hub_siren_start():  # noqa: ANN202
        """Sound the siren. POST {"sub_id": 1} (defaults to 1).

        WARNING: this triggers a real 100+ dB siren if one is paired
        and online. Use /test for a self-clearing 2-second burst.
        """
        err = _admin_or_503()
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        sub_id = int(body.get("sub_id", 1))
        try:
            return jsonify({"ok": True,
                            "payload": axpro_admin.start_siren(sub_id)})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc),
                            "payload": getattr(exc, "payload", None)}), 502

    @app.post("/api/sensors/hub/siren/stop")
    def hub_siren_stop():  # noqa: ANN202
        """Silence the siren. POST {"sub_id": 1}."""
        err = _admin_or_503()
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        sub_id = int(body.get("sub_id", 1))
        try:
            return jsonify({"ok": True,
                            "payload": axpro_admin.stop_siren(sub_id)})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc),
                            "payload": getattr(exc, "payload", None)}), 502

    @app.post("/api/sensors/hub/siren/test")
    def hub_siren_test():  # noqa: ANN202
        """Short burst — start, wait `duration_s` (default 2, max 10),
        then stop. The stop fires from a try/finally so an interrupted
        test can't leave the siren ringing."""
        err = _admin_or_503()
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        sub_id = int(body.get("sub_id", 1))
        duration = float(body.get("duration_s", 2.0))
        try:
            return jsonify({"ok": True,
                            "payload": axpro_admin.test_siren(sub_id, duration_s=duration)})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc),
                            "payload": getattr(exc, "payload", None)}), 502

    @app.patch("/api/sensors/zones/<int:zone_id>/config")
    def hub_zone_config_patch(zone_id: int):  # noqa: ANN202
        """Set a single key on the zone config — used for the
        `armNoBypassEnabled` flag in particular, which decides whether
        the hub auto-bypasses a faulty zone on arm. POST body:
            {"key": "armNoBypassEnabled", "value": true}"""
        err = _admin_or_503()
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        key = str(body.get("key") or "")
        if not key:
            return jsonify({"ok": False, "error": "missing 'key'"}), 400
        if "value" not in body:
            return jsonify({"ok": False, "error": "missing 'value'"}), 400
        try:
            payload = axpro_admin.set_zone_config_flag(
                zone_id, key=key, value=body["value"],
            )
            return jsonify({"ok": True, "payload": payload})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc),
                            "payload": getattr(exc, "payload", None)}), 502

    @app.get("/api/presets/current")
    def list_current_presets():  # noqa: ANN202
        """Per-camera "what preset is each camera at right now?".

        Drives zone-overlay filtering on the Live page. `null` means
        ITIPS has not commanded a goto for that camera since boot (or
        a manual jog cleared it) — the dashboard treats this as
        "hide all preset-bound zones, evaluate only always-active ones".
        """
        if preset_state is None:
            return jsonify({"available": False, "cameras": {}})
        snapshot = preset_state.all()
        return jsonify({
            "available": True,
            # JSON object keys must be strings.
            "cameras": {str(cam): name for cam, name in snapshot.items()},
        })

    @app.get("/api/cameras/<int:camera_id>/base-preset")
    def get_camera_base_preset(camera_id: int):  # noqa: ANN202
        """The preset the system pans to when this camera's RTSP
        stream comes back after a disrupt. `null` = no auto-restore."""
        if camera_settings is None:
            return jsonify({"available": False, "base_preset_name": None})
        return jsonify({
            "available": True,
            "camera_id": camera_id,
            "base_preset_name": camera_settings.get(camera_id).base_preset_name,
        })

    @app.put("/api/cameras/<int:camera_id>/base-preset")
    def set_camera_base_preset(camera_id: int):  # noqa: ANN202
        if camera_settings is None:
            return jsonify({"ok": False, "error": "camera_settings not wired"}), 503
        body = request.get_json(silent=True) or {}
        # `null` / "" / missing → clear the binding (no auto-restore).
        raw = body.get("name") if "name" in body else body.get("base_preset_name")
        camera_settings.set_base_preset(camera_id, raw)
        return jsonify({
            "ok": True,
            "camera_id": camera_id,
            "base_preset_name": camera_settings.get(camera_id).base_preset_name,
        })

    @app.get("/api/cameras/<int:camera_id>/presets")
    def list_camera_presets(camera_id: int):  # noqa: ANN202
        """Camera's onboard PTZ presets — drives the binding dropdown."""
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        try:
            presets = client.ptz.list_presets()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({
            "ok": True,
            "camera_id": camera_id,
            "presets": [
                {"index": p.index, "name": p.name,
                 "pan": p.pan, "tilt": p.tilt, "zoom": p.zoom}
                for p in presets
            ],
        })

    @app.post("/api/cameras/<int:camera_id>/presets")
    def save_camera_preset(camera_id: int):  # noqa: ANN202
        """Save the camera's *current* PTZ position as a named preset.

        Backs the calibration wizard's "Save position" button. Body:
            {"name": "zone_1_gate", "index": 12}   # both optional

        If `index` is omitted we pick the next free slot in [1..255].
        If `name` is omitted we use `preset_<index>`. Returns the
        preset record the camera actually persisted (which may differ
        from the requested name on firmwares that reject naming via
        configManager — the index is always authoritative).
        """
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        idx_raw = body.get("index")
        try:
            idx = int(idx_raw) if idx_raw is not None else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "index must be an int"}), 400
        try:
            preset = client.ptz.save_current_as_preset(name, index=idx)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        # If the operator asked for a name but the camera kept its own
        # auto-name (firmware refused naming on every known path), tell
        # them — silent fallback to "Preset5" was confusing.
        name_warning = None
        if name and (preset.name or "").strip() != name.strip():
            name_warning = (
                f"Camera kept its auto-name '{preset.name}' instead of "
                f"'{name}'. This firmware does not accept renaming over HTTP; "
                f"rename in the camera's web UI if you need the custom label."
            )
        payload = {
            "ok": True,
            "camera_id": camera_id,
            "preset": {"index": preset.index, "name": preset.name,
                       "pan": preset.pan, "tilt": preset.tilt, "zoom": preset.zoom},
        }
        if name_warning:
            payload["name_warning"] = name_warning
        # Saving snapshots the camera's *current* PTZ position into a
        # preset — by definition the camera is now at that preset, so
        # mirror it into the tracker for zone gating.
        if preset_state is not None:
            preset_state.record_goto(camera_id, preset.name)
        return jsonify(payload), 201

    @app.delete("/api/cameras/<int:camera_id>/presets/<int:index>")
    def delete_camera_preset(camera_id: int, index: int):  # noqa: ANN202
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        try:
            client.ptz.delete_preset(index)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True})

    @app.post("/api/cameras/<int:camera_id>/presets/<int:index>/goto")
    def goto_camera_preset(camera_id: int, index: int):  # noqa: ANN202
        """Jump the PTZ to a preset by index. Used by the wizard's
        Test Pan flow — pan away, then back."""
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        # Resolve the preset's name *before* the goto so we can mirror
        # the new orientation into preset_state for zone gating. The
        # listing is cheap (one HTTP call) and the alternative — a second
        # list after goto — would race with anything else that touches
        # presets in parallel.
        target_name = None
        try:
            for p in client.ptz.list_presets():
                if p.index == index:
                    target_name = p.name
                    break
        except Exception:
            target_name = None
        try:
            client.ptz.go_to_preset(index)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        if preset_state is not None and target_name:
            preset_state.record_goto(camera_id, target_name)
        return jsonify({"ok": True, "preset_name": target_name})

    @app.post("/api/sensors/zones/<int:zone_id>/calibrate")
    def calibrate_sensor_zone(zone_id: int):  # noqa: ANN202
        """Atomic save-preset + bind-sensor in one operator action.

        Body:
            {"camera_id": 4, "name": "zone_1_gate",
             "sensor_type": "doorContact",
             "description": "front gate magnetic contact"}

        Steps:
          1. Save the camera's current position as a named preset.
          2. Upsert SensorMap(zone_id) → (camera, preset_name).
          3. Return both so the wizard can render the result and
             chain a Test Pan immediately.

        Doing this atomically is the whole point — "I saved a preset
        and forgot to bind it" is the single most common install
        failure mode for camera-to-sensor systems.
        """
        if sensor_map is None:
            return jsonify({"ok": False, "error": "sensor map not wired"}), 503
        body = request.get_json(silent=True) or {}
        try:
            camera_id = int(body["camera_id"])
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "error": "camera_id is required (int)"}), 400
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": f"unknown camera {camera_id}"}), 404
        # Default to a deterministic name so duplicate Calibrate clicks
        # on the same zone reuse the same camera-side preset slot
        # instead of leaking new ones forever.
        name = str(body.get("name") or f"zone_{zone_id}").strip()
        # If a previous binding exists with a preset of the same name,
        # reuse its index so the camera's preset table stays clean.
        reuse_index = None
        previous = sensor_map.get(zone_id)
        if previous and previous.camera_id == camera_id:
            for p in client.ptz.list_presets():
                if p.name == previous.preset_name:
                    reuse_index = p.index
                    break
        try:
            preset = client.ptz.save_current_as_preset(name, index=reuse_index)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False,
                            "error": f"saving preset failed: {exc}"}), 502
        try:
            from itips.sensors.sensor_map import SensorMapping
            mapping = SensorMapping(
                zone_id=zone_id,
                camera_id=camera_id,
                preset_name=preset.name,
                sensor_type=str(body.get("sensor_type", "")),
                description=str(body.get("description", "")),
                metadata={"preset_index": preset.index,
                          **(dict(body.get("metadata") or {}))},
            )
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False,
                            "error": f"invalid mapping: {exc}"}), 400
        sensor_map.upsert(mapping)
        # Calibrate also leaves the camera at the saved preset.
        if preset_state is not None:
            preset_state.record_goto(camera_id, preset.name)
        return jsonify({
            "ok": True,
            "mapping": _sensor_mapping_to_dict(mapping),
            "preset": {"index": preset.index, "name": preset.name,
                       "pan": preset.pan, "tilt": preset.tilt, "zoom": preset.zoom},
        }), 201

    @app.post("/api/sensors/zones/<int:zone_id>/test-pan")
    def test_pan_sensor_zone(zone_id: int):  # noqa: ANN202
        """Verify a sensor's binding by panning the bound camera
        through home → bound preset and returning a snapshot URL.

        The two-step pan (away then back) makes the motion *visible*
        — without it, the camera may already be sitting on the
        preset, in which case nothing happens and the operator can't
        tell whether the command worked at all."""
        if sensor_map is None:
            return jsonify({"ok": False, "error": "sensor map not wired"}), 503
        mapping = sensor_map.get(zone_id)
        if mapping is None:
            return jsonify({"ok": False,
                            "error": f"zone {zone_id} has no binding"}), 404
        client = dahua_manager.get(mapping.camera_id)
        if client is None:
            return jsonify({"ok": False,
                            "error": f"camera {mapping.camera_id} not connected"}), 502
        # Pan away first so the motion is obvious. Falls back to a
        # no-op if go_home isn't supported on this PTZ.
        try:
            client.ptz.go_home()
        except Exception:
            logger.info("test-pan: go_home() unsupported, skipping")
        # Brief pause so the operator can see the camera leave the
        # preset before it returns — pure UX, not a correctness need.
        time.sleep(0.8)
        try:
            ok = client.ptz.goto_preset_by_name(mapping.preset_name)
            if not ok:
                # Fallback to the cached index if name lookup failed
                # (some firmwares mangle the name on save).
                preset_index = (mapping.metadata or {}).get("preset_index")
                if preset_index is not None:
                    client.ptz.go_to_preset(int(preset_index))
                    ok = True
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False,
                            "error": f"goto failed: {exc}"}), 502
        if not ok:
            return jsonify({
                "ok": False,
                "error": f"preset '{mapping.preset_name}' not found on camera",
            }), 502
        # Test-pan leaves the camera at the bound preset.
        if preset_state is not None:
            preset_state.record_goto(mapping.camera_id, mapping.preset_name)
        # Snapshot URL with a cache-buster so the wizard refreshes
        # rather than serving an old frame.
        ts = int(time.time() * 1000)
        return jsonify({
            "ok": True,
            "mapping": _sensor_mapping_to_dict(mapping),
            "snapshot_url": f"/api/snapshot/{mapping.camera_id}?t={ts}",
        })

    # ─── test console ──────────────────────────────────────────────
    # Routes prefixed `/api/test/*` are for the in-dashboard Test Console.
    # They give an operator a live view into the raw Dahua event stream
    # plus quick-action knobs that talk to the camera HTTP API and to the
    # local AlertEngine for synthetic-event injection.

    @app.get("/api/test/events/recent")
    def test_events_recent():  # noqa: ANN202
        if event_tap is None:
            return jsonify({"events": []})
        limit = request.args.get("limit", type=int, default=50)
        return jsonify({"events": event_tap.recent(limit=limit)})

    @app.get("/api/test/events/stream")
    def test_events_stream():  # noqa: ANN202
        if event_tap is None:
            return Response("event tap disabled\n", status=503,
                            mimetype="text/plain")

        def gen():
            cursor = 0
            yield ": connected\n\n"
            last_send = time.monotonic()
            # Replay the buffer once so a fresh subscriber gets context.
            initial, cursor = event_tap.since(cursor)
            for ev in initial:
                yield f"data: {json.dumps(ev)}\n\n"
                last_send = time.monotonic()
            while True:
                new_items, cursor = event_tap.since(cursor)
                if new_items:
                    for ev in new_items:
                        yield f"data: {json.dumps(ev)}\n\n"
                    last_send = time.monotonic()
                elif (time.monotonic() - last_send) > 15:
                    yield ": keepalive\n\n"
                    last_send = time.monotonic()
                time.sleep(0.4)

        return Response(
            stream_with_context(gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/test/ptz/<int:camera_id>/<direction>")
    def test_ptz_jog(camera_id: int, direction: str):  # noqa: ANN202
        """Direction-button jog. POST with {"action": "start"|"stop", "speed": 4}."""
        body = request.get_json(silent=True) or {}
        action = body.get("action", "start")
        speed = int(body.get("speed", 4))
        client = dahua_manager.get(camera_id)
        if client is None:
            return jsonify({"ok": False, "error": "unknown camera"}), 404
        try:
            if action == "stop":
                client.ptz.jog_stop(direction)
            else:
                client.ptz.jog_start(direction, speed=speed)
                # A manual jog moves the camera off whatever preset it
                # was at — clear the tracker so preset-gated zones hide
                # instead of firing on the new (unknown) view. The 5-min
                # grace tells the recovery watcher to keep its hands off
                # while the operator is actively driving the PTZ.
                if preset_state is not None:
                    preset_state.clear(camera_id, manual_grace_s=300.0)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            # Camera-side rejection (channel mismatch, unsupported code,
            # bad permissions) lands here. Return the camera's own
            # message so operators don't have to grep logs.
            return jsonify({
                "ok": False,
                "error": str(exc),
                "hint": "camera returned the request but rejected the PTZ command "
                        "— common causes: PTZ on a different channel "
                        "(set ITIPS_PTZ_<N>_CHANNEL), insufficient ONVIF "
                        "permissions, or the camera doesn't support this code.",
            }), 502
        return jsonify({"ok": True})

    @app.post("/api/test/simulate/<event_type>")
    def test_simulate(event_type: str):  # noqa: ANN202
        """Inject a synthetic event into the AlertEngine.

        Useful for verifying the incident lifecycle without waiting for
        a real camera trigger. Available types:
          face_intruder, face_known, line_crossing, intrusion,
          loitering, plate, fire, smoke
        """
        if alert_engine is None:
            return jsonify({"ok": False, "error": "alert engine unavailable"}), 503
        body = request.get_json(silent=True) or {}
        camera_id = int(body.get("camera_id", 1))
        try:
            _simulate(event_type, alert_engine, camera_id, body)
        except KeyError:
            return jsonify({"ok": False, "error": f"unknown event_type {event_type!r}"}), 400
        return jsonify({"ok": True, "event_type": event_type, "camera_id": camera_id})

    @app.post("/api/test/inbound/<path:endpoint>")
    def test_inbound_proxy(endpoint: str):  # noqa: ANN202
        """Forward to the local 8443 inbound API with the configured bearer.

        `<path:endpoint>` rather than the default string converter so
        URLs with slashes (`personnel/sync`, `maintenance/window`) reach
        this handler instead of Flask's HTML 404 page.

        The Test Console lives on port 5050; the inbound API is on 8443
        with bearer auth. Calling 8443 directly from the browser would
        require CORS + token plumbing in the client. This proxy keeps
        the bearer server-side.
        """
        valid = {"personnel/sync", "config", "maintenance/window", "commands", "firmware/update"}
        if endpoint not in valid:
            return jsonify({"ok": False, "error": f"unknown endpoint {endpoint!r}"}), 404
        token = settings.api.inbound_token
        if not token:
            return jsonify({"ok": False, "error": "ITIPS_INBOUND_TOKEN not set"}), 503
        url = f"http://127.0.0.1:{settings.api.inbound_port}/local/api/v1/{endpoint}"
        payload = request.get_json(silent=True) or {}
        try:
            r = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
        except requests.RequestException as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        body = r.text
        try:
            body = r.json()
        except ValueError:
            pass
        return jsonify({"ok": r.status_code < 400, "status": r.status_code, "response": body})


# ─── helpers ─────────────────────────────────────────────────────────


def _simulate(event_type: str, engine, camera_id: int, body: dict[str, Any]) -> None:
    """Map a simulator key onto an AlertEngine handler call."""
    if event_type == "face_intruder":
        engine.handle_face_intruder(
            camera_id=camera_id,
            face_bbox=body.get("bbox") or (100, 100, 220, 260),
            name="INTRUDER",
        )
    elif event_type == "face_known":
        engine.handle_personnel_seen(
            camera_id=camera_id,
            person_uid=body.get("person_uid", "sim-0001"),
            group_id=body.get("group_id", "10000"),
            name=body.get("name", "Sim Worker"),
            similarity=int(body.get("similarity", 85)),
        )
    elif event_type == "line_crossing":
        engine.handle_behaviour_alert_simple(
            camera_id=camera_id,
            alert_type="line_crossing",
            details={"direction": body.get("direction", "Any"), "rule_name": "sim"},
        )
    elif event_type == "intrusion":
        engine.handle_behaviour_alert_simple(
            camera_id=camera_id,
            alert_type="intrusion",
            details={"action": body.get("action", "Cross"), "rule_name": "sim"},
        )
    elif event_type == "loitering":
        engine.handle_behaviour_alert_simple(
            camera_id=camera_id,
            alert_type="loitering",
            details={"rule_name": "sim"},
        )
    elif event_type == "plate":
        engine.handle_plate_capture(
            camera_id=camera_id,
            plate_number=body.get("plate_number", "LAG-123-XY"),
            plate_color=body.get("plate_color", "White"),
            vehicle_color=body.get("vehicle_color", "Black"),
            speed=body.get("speed"),
        )
    elif event_type == "fire":
        engine.handle_fire(camera_id=camera_id, details={"rule": "sim"})
    elif event_type == "smoke":
        engine.handle_smoke(camera_id=camera_id, details={"color": body.get("color", "Black")})
    else:
        raise KeyError(event_type)


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


def _zone_to_dict(zone) -> dict:
    return {
        "zone_id": zone.zone_id,
        "zone_type": zone.zone_type,
        "points": [list(p) for p in zone.points],
        "name": zone.name,
        "direction": zone.direction,
        "preset_name": getattr(zone, "preset_name", None),
        "metadata": dict(zone.metadata or {}),
    }


def _zone_from_dict(body: dict):
    """Lazy ml/ import so the dashboard works without the ml extras."""
    from itips.ml.zone_store import Zone
    preset_raw = body.get("preset_name")
    preset_name = str(preset_raw).strip() if preset_raw else None
    return Zone(
        zone_id=str(body["zone_id"]),
        zone_type=str(body["zone_type"]),
        points=[tuple(p) for p in body.get("points", [])],
        name=str(body.get("name", "")),
        direction=str(body.get("direction", "Any")),
        preset_name=preset_name or None,
        metadata=dict(body.get("metadata") or {}),
    )


# ─── ML Lab helpers ──────────────────────────────────────────────────


def _truthy(v: str | None) -> bool:
    return (v or "").lower() in {"1", "true", "yes", "on"}


def _engine_status(engine) -> dict:
    if engine is None:
        return {"wired": False, "ready": False}
    try:
        ready = bool(engine.is_ready())
    except Exception:
        ready = False
    return {"wired": True, "ready": ready}


def _decode_upload(request) -> "np.ndarray | None":  # type: ignore[name-defined]
    """Multipart image → BGR ndarray. Accepts `image` or `file` field."""
    f = request.files.get("image") or request.files.get("file")
    if f is None:
        return None
    blob = f.read()
    if not blob:
        return None
    import cv2
    import numpy as np
    buf = np.frombuffer(blob, dtype="uint8")
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return frame


def _behavior_alert_to_dict(a) -> dict:
    return {
        "alert_type": a.alert_type,
        "zone_id": a.zone_id,
        "zone_name": a.zone_name,
        "track_id": a.track_id,
        "class_name": a.class_name,
        "bbox": list(a.bbox),
        "details": dict(a.details),
    }


def _sensor_mapping_to_dict(m) -> dict:
    return {
        "zone_id": m.zone_id,
        "camera_id": m.camera_id,
        "preset_name": m.preset_name,
        "sensor_type": m.sensor_type,
        "description": m.description,
        "metadata": dict(m.metadata or {}),
    }


def _detection_to_dict(d, frame_w: int, frame_h: int) -> dict:
    """Includes the bottom-center anchor — what region containment uses."""
    x1, y1, x2, y2 = d.bbox
    ax, ay = (x1 + x2) / 2.0, y2
    return {
        "class_name": d.class_name,
        "confidence": round(float(d.confidence), 4),
        "bbox": [round(float(v), 1) for v in d.bbox],
        "anchor_px": [round(ax, 1), round(ay, 1)],
        "anchor_norm": [
            round(ax / max(1, frame_w), 4),
            round(ay / max(1, frame_h), 4),
        ],
    }
