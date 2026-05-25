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
        return jsonify({
            "workers": [
                {
                    "person_id": rec.person_id,
                    "full_name": rec.full_name,
                    "cameras": rec.per_camera,
                }
                for rec in personnel_store.list_all()
            ],
            "available_cameras": dahua_manager.camera_ids(),
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

        # Mirror into the Jetson face DB so cameras without native
        # faceRecognitionServer can still recognise this worker. Soft
        # failure: a missing FaceEngine, missing ML extras, or a no-face
        # enrolment image must not block the native-camera path.
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
        """Per-camera capability vector the event worker is currently
        routing on. Useful to verify which fallback paths are armed."""
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
            # Replay the buffer once so a fresh subscriber gets context.
            initial, cursor = event_tap.since(cursor)
            for ev in initial:
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                new_items, cursor = event_tap.since(cursor)
                for ev in new_items:
                    yield f"data: {json.dumps(ev)}\n\n"
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
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
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

    @app.post("/api/test/inbound/<endpoint>")
    def test_inbound_proxy(endpoint: str):  # noqa: ANN202
        """Forward to the local 8443 inbound API with the configured bearer.

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
        "metadata": dict(zone.metadata or {}),
    }


def _zone_from_dict(body: dict):
    """Build a `Zone` from a JSON request body.

    Imported lazily so the dashboard can be wired without the ML
    extras installed — only the zone routes need it, and they'll
    have short-circuited on `zone_store is None` before reaching here.
    """
    from itips.ml.zone_store import Zone
    return Zone(
        zone_id=str(body["zone_id"]),
        zone_type=str(body["zone_type"]),
        points=[tuple(p) for p in body.get("points", [])],
        name=str(body.get("name", "")),
        direction=str(body.get("direction", "Any")),
        metadata=dict(body.get("metadata") or {}),
    )
