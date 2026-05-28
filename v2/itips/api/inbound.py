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

For `add`/`update` we enrol the JPEG into the local face engine (InsightFace
embeddings) keyed by `person_id`; on `deactivate` we drop that embedding. The
Dahua cameras' onboard face DB is no longer used.

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
from itips.camera.dahua_manager import DahuaManager

logger = logging.getLogger(__name__)


class InboundApiServer(threading.Thread):
    def __init__(
        self,
        *,
        dahua_manager: DahuaManager,
        personnel_store: PersonnelStore,
        face_engine: Any = None,
    ) -> None:
        super().__init__(name="api-inbound", daemon=True)
        self._dahua = dahua_manager
        self._personnel = personnel_store
        self._face_engine = face_engine
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
            result = _apply_personnel_sync(
                payload, self._personnel, self._face_engine,
            )
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
    personnel: PersonnelStore,
    face_engine: Any,
) -> dict[str, Any]:
    action = payload.get("action")
    person_id = payload.get("person_id")
    if not person_id or action not in {"add", "update", "deactivate"}:
        return {"synced": False, "reason": "invalid payload"}

    if action == "deactivate":
        return _deactivate_person(str(person_id), personnel, face_engine)

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
        personnel=personnel,
        face_engine=face_engine,
    )


def _add_or_update_person(
    *,
    person_id: str,
    full_name: str,
    jpeg: bytes,
    personnel: PersonnelStore,
    face_engine: Any,
) -> dict[str, Any]:
    """Enrol the worker JPEG into the local face engine, keyed by person_id."""
    if face_engine is None:
        return {"synced": False, "person_id": person_id,
                "reason": "face engine unavailable"}
    try:
        face_engine.enroll(
            person_id=person_id, full_name=full_name, image_bytes=jpeg,
        )
    except ValueError as exc:
        # No detectable face in the supplied image.
        return {"synced": False, "person_id": person_id, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("face enrol failed for %s: %s", person_id, exc)
        return {"synced": False, "person_id": person_id,
                "reason": f"enrol failed: {exc.__class__.__name__}"}

    personnel.upsert(person_id=person_id, full_name=full_name, per_camera={})
    return {"synced": True, "person_id": person_id}


def _deactivate_person(
    person_id: str,
    personnel: PersonnelStore,
    face_engine: Any,
) -> dict[str, Any]:
    known = personnel.get(person_id) is not None
    removed_ml = False
    if face_engine is not None:
        try:
            removed_ml = face_engine.remove(person_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("face remove failed for %s: %s", person_id, exc)
    personnel.delete(person_id)
    if not known and not removed_ml:
        return {"synced": True, "person_id": person_id, "note": "not found"}
    return {
        "synced": True,
        "person_id": person_id,
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
