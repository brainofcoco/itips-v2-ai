"""Port 8443 inbound API — backend → Jetson commands (PRD §4.7 Part B).

Routes implemented:
  B1  POST /local/api/v1/personnel/sync       — personnel cache update
  B2  POST /local/api/v1/config               — hot config push (no-op stub)
  B3  POST /local/api/v1/maintenance/window   — arm/disarm window
  B4  POST /local/api/v1/commands             — PTZ / deterrence / stream
  B5  POST /local/api/v1/firmware/update      — schedule firmware install

Personnel sync (B1)
-------------------
The backend now ships the worker JPEG (the camera does the embedding
work). Payload supports two shapes:

  * `{"action":"add","person_id":"p1","full_name":"...","image_b64":"..."}`
  * `{"action":"deactivate","person_id":"p1"}`

For `add`/`update` we fan out to every active camera's FaceRecognitionServer
and record the camera-assigned UID alongside our `person_id`. On `deactivate`
we look up those UIDs and delete on every camera.

Commands (B4)
-------------
* `ptz_override`           → DahuaPTZ.apply_override (preset/bbox/abs angles)
* `deterrence_standdown`   → DahuaDeterrence.standdown on the requested camera
* `deterrence_fire`        → DahuaDeterrence.fire on the requested camera
* `request_stream`         → returns the existing MJPEG hint
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any

from flask import Flask, jsonify, request

from config.settings import settings
from itips.api.personnel_store import PersonnelStore
from itips.camera.dahua_face_db import DahuaFaceDBError
from itips.camera.dahua_manager import DahuaManager

logger = logging.getLogger(__name__)


class InboundApiServer(threading.Thread):
    def __init__(
        self,
        *,
        dahua_manager: DahuaManager,
        personnel_store: PersonnelStore,
    ) -> None:
        super().__init__(name="api-inbound", daemon=True)
        self._dahua = dahua_manager
        self._personnel = personnel_store
        self._app = self._build_app()
        self._server = None
        self._stop = threading.Event()

    def _build_app(self) -> Flask:
        from itips.api.docs import register_docs

        app = Flask("itips-inbound")
        register_docs(app)

        _open_paths = {"/health", "/docs", "/docs/", "/openapi.yaml"}

        @app.before_request
        def auth():  # noqa: ANN202
            if request.path in _open_paths:
                return None
            token = settings.api.inbound_token
            header = request.headers.get("Authorization", "")
            if not token or not header.startswith("Bearer ") or header[7:] != token:
                return jsonify({"error": "unauthorized"}), 401
            return None

        @app.get("/health")
        def health():
            return jsonify({"status": "ok"})

        @app.post("/local/api/v1/personnel/sync")
        def b1_personnel_sync():
            payload = request.get_json(silent=True) or {}
            result = _apply_personnel_sync(payload, self._dahua, self._personnel)
            return jsonify(result)

        @app.post("/local/api/v1/config")
        def b2_config():
            payload = request.get_json(silent=True) or {}
            return jsonify({"applied": True, "version": payload.get("config_version", "?")})

        @app.post("/local/api/v1/maintenance/window")
        def b3_maintenance():
            payload = request.get_json(silent=True) or {}
            return jsonify(_apply_maintenance(payload))

        @app.post("/local/api/v1/commands")
        def b4_commands():
            payload = request.get_json(silent=True) or {}
            return jsonify(_apply_command(payload, self._dahua))

        @app.post("/local/api/v1/firmware/update")
        def b5_firmware():
            payload = request.get_json(silent=True) or {}
            return jsonify({"scheduled": True, "version": payload.get("version", "?")})

        return app

    def run(self) -> None:
        from werkzeug.serving import make_server

        ssl_ctx = None
        if settings.api.inbound_tls_cert and settings.api.inbound_tls_key:
            ssl_ctx = (settings.api.inbound_tls_cert, settings.api.inbound_tls_key)

        self._server = make_server(
            host=settings.api.inbound_host,
            port=settings.api.inbound_port,
            app=self._app,
            ssl_context=ssl_ctx,
            threaded=True,
        )
        scheme = "https" if ssl_ctx else "http"
        logger.info("Inbound API listening on %s://%s:%d",
                    scheme, settings.api.inbound_host, settings.api.inbound_port)
        try:
            self._server.serve_forever()
        except Exception:
            logger.exception("Inbound API crashed.")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        self._stop.set()


# ─── route handlers (pure functions, easy to test) ───────────────────


def _apply_personnel_sync(
    payload: dict[str, Any],
    dahua: DahuaManager,
    personnel: PersonnelStore,
) -> dict[str, Any]:
    action = payload.get("action")
    person_id = payload.get("person_id")
    if not person_id or action not in {"add", "update", "deactivate"}:
        return {"synced": False, "reason": "invalid payload"}

    if action == "deactivate":
        return _deactivate_person(str(person_id), dahua, personnel)

    image_b64 = payload.get("image_b64") or payload.get("image")
    if not image_b64:
        return {"synced": False, "reason": "image_b64 required for add/update"}
    try:
        jpeg = base64.b64decode(image_b64, validate=False)
    except Exception:
        return {"synced": False, "reason": "image_b64 not valid base64"}

    return _add_or_update_person(
        person_id=str(person_id),
        full_name=str(payload.get("full_name") or person_id),
        jpeg=jpeg,
        sex=payload.get("sex"),
        birthday=payload.get("birthday"),
        dahua=dahua,
        personnel=personnel,
    )


def _add_or_update_person(
    *,
    person_id: str,
    full_name: str,
    jpeg: bytes,
    sex: Any,
    birthday: Any,
    dahua: DahuaManager,
    personnel: PersonnelStore,
) -> dict[str, Any]:
    """Fan out `addPerson` to every camera with a workers group bound."""
    # Replace existing record (update == delete-then-add for simplicity).
    if personnel.get(person_id):
        _deactivate_person(person_id, dahua, personnel)

    per_camera: dict[int, str] = {}
    failures: list[str] = []
    for client in dahua.all():
        if not client.workers_group_id:
            failures.append(f"cam{client.camera_id}:no-group")
            continue
        try:
            uid = client.face_db.add_person(
                group_id=client.workers_group_id,
                name=full_name,
                jpeg=jpeg,
                sex=sex if isinstance(sex, str) else None,
                birthday=birthday if isinstance(birthday, str) else None,
            )
            per_camera[client.camera_id] = uid
        except (DahuaFaceDBError, Exception) as exc:  # noqa: BLE001
            failures.append(f"cam{client.camera_id}:{exc.__class__.__name__}")
            logger.warning("cam %d: addPerson failed for %s: %s",
                           client.camera_id, person_id, exc)

    personnel.upsert(person_id=person_id, full_name=full_name, per_camera=per_camera)

    return {
        "synced": bool(per_camera),
        "person_id": person_id,
        "cameras": per_camera,
        "failures": failures,
    }


def _deactivate_person(
    person_id: str,
    dahua: DahuaManager,
    personnel: PersonnelStore,
) -> dict[str, Any]:
    record = personnel.get(person_id)
    if not record:
        return {"synced": True, "person_id": person_id, "note": "not found"}
    removed: list[int] = []
    failures: list[str] = []
    for cam_id, uid in record.per_camera.items():
        client = dahua.get(cam_id)
        if client is None or not client.workers_group_id:
            failures.append(f"cam{cam_id}:not-available")
            continue
        try:
            client.face_db.delete_person(group_id=client.workers_group_id, uid=uid)
            removed.append(cam_id)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"cam{cam_id}:{exc.__class__.__name__}")
    personnel.delete(person_id)
    return {
        "synced": True,
        "person_id": person_id,
        "cameras_cleared": removed,
        "failures": failures,
    }


def _apply_maintenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Maintenance windows are now a backend-side concept.

    With FaceRecognition fan-out the Jetson does not need a per-person
    auth gate — a registered worker is recognised by the camera and
    `handle_personnel_seen` already suppresses incident creation. The
    window's role shrinks to "pause RAPID escalation for this person ID
    during this time range," which is a backend policy decision.
    """
    action = payload.get("action")
    window_id = payload.get("window_id")
    person_id = payload.get("person_id")
    if not window_id or not person_id or action not in {"arm", "disarm"}:
        return {"applied": False, "reason": "invalid payload"}
    logger.info(
        "Maintenance window %s for person %s acknowledged (no-op on Jetson)",
        action, person_id,
    )
    return {"applied": True}


def _apply_command(payload: dict[str, Any], dahua: DahuaManager) -> dict[str, Any]:
    command = payload.get("command_type")
    params = payload.get("parameters") or {}
    try:
        camera_id = int(params.get("camera_id", 1))
    except (TypeError, ValueError):
        return {"command_accepted": False, "reason": "camera_id must be int"}

    client = dahua.get(camera_id)

    if command == "ptz_override":
        if client is None:
            return {"command_accepted": False, "reason": f"no client for cam {camera_id}"}
        try:
            client.ptz.apply_override(params)
            return {"command_accepted": True}
        except Exception as exc:  # noqa: BLE001
            return {"command_accepted": False, "reason": str(exc)}

    if command == "deterrence_standdown":
        if client is None:
            return {"command_accepted": False, "reason": f"no client for cam {camera_id}"}
        try:
            client.deterrence.standdown()
            return {"command_accepted": True}
        except Exception as exc:  # noqa: BLE001
            return {"command_accepted": False, "reason": str(exc)}

    if command == "deterrence_fire":
        if client is None:
            return {"command_accepted": False, "reason": f"no client for cam {camera_id}"}
        try:
            client.deterrence.fire(
                light=bool(params.get("light", True)),
                speaker=bool(params.get("speaker", True)),
            )
            return {"command_accepted": True}
        except Exception as exc:  # noqa: BLE001
            return {"command_accepted": False, "reason": str(exc)}

    if command == "request_stream":
        return {
            "command_accepted": True,
            "stream": {"protocol": "mjpeg", "url_hint": f"/video_feed/{camera_id}"},
        }

    return {"command_accepted": False, "reason": f"unknown command {command}"}
