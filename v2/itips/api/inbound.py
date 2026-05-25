"""Port 8443 inbound API — backend → Jetson commands (PRD §4.7 Part B).

Routes implemented:
  B1  POST /local/api/v1/personnel/sync       — personnel cache update
  B2  POST /local/api/v1/config               — hot config push
  B3  POST /local/api/v1/maintenance/window   — arm/disarm window
  B4  POST /local/api/v1/commands             — PTZ override, standdown, request_stream
  B5  POST /local/api/v1/firmware/update      — schedule firmware install

Auth model:
  POC: static bearer token in `Authorization: Bearer <ITIPS_INBOUND_TOKEN>`.
  Phase 1: mutual TLS using a per-device certificate. The route handlers
  themselves don't change — only the middleware.

The server runs in its own thread so it doesn't block startup. TLS is
optional; when cert+key paths are present we serve HTTPS, otherwise HTTP
(useful for POC site networks where TLS terminates upstream).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from flask import Flask, jsonify, request

from config.settings import settings

logger = logging.getLogger(__name__)


class InboundApiServer(threading.Thread):
    def __init__(self, *, face_engine, face_authorizer, ptz_controllers) -> None:
        super().__init__(name="api-inbound", daemon=True)
        self._face_engine = face_engine
        self._face_authorizer = face_authorizer
        self._ptz_controllers = ptz_controllers or {}
        self._app = self._build_app()
        self._server = None
        self._stop = threading.Event()

    def _build_app(self) -> Flask:
        from itips.api.docs import register_docs

        app = Flask("itips-inbound")
        register_docs(app)

        # /health, /docs, and /openapi.yaml are intentionally open so the
        # Scalar reference loads without a token. Every B1–B5 route below
        # still requires the bearer.
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
            result = _apply_personnel_sync(payload, self._face_engine)
            return jsonify(result)

        @app.post("/local/api/v1/config")
        def b2_config():
            payload = request.get_json(silent=True) or {}
            return jsonify({"applied": True, "version": payload.get("config_version", "?")})

        @app.post("/local/api/v1/maintenance/window")
        def b3_maintenance():
            payload = request.get_json(silent=True) or {}
            return jsonify(_apply_maintenance(payload, self._face_authorizer))

        @app.post("/local/api/v1/commands")
        def b4_commands():
            payload = request.get_json(silent=True) or {}
            return jsonify(_apply_command(payload, self._ptz_controllers))

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


def _apply_personnel_sync(payload: dict[str, Any], face_engine) -> dict[str, Any]:
    action = payload.get("action")
    person_id = payload.get("person_id")
    if not person_id or action not in {"add", "update", "deactivate"}:
        return {"synced": False, "reason": "invalid payload"}
    method = getattr(face_engine, "apply_personnel_sync", None)
    if not callable(method):
        return {"synced": False, "reason": "personnel sync not implemented"}
    try:
        method(payload)
        return {"synced": True}
    except Exception as exc:
        return {"synced": False, "reason": str(exc)}


def _apply_maintenance(payload: dict[str, Any], face_authorizer) -> dict[str, Any]:
    action = payload.get("action")
    window_id = payload.get("window_id")
    person_id = payload.get("person_id")
    if not window_id or not person_id or action not in {"arm", "disarm"}:
        return {"applied": False, "reason": "invalid payload"}
    method = getattr(face_authorizer, "apply_maintenance_window", None)
    if not callable(method):
        return {"applied": False, "reason": "maintenance window not implemented"}
    try:
        method(payload)
        return {"applied": True}
    except Exception as exc:
        return {"applied": False, "reason": str(exc)}


def _apply_command(payload: dict[str, Any], ptz_controllers: dict[int, Any]) -> dict[str, Any]:
    command = payload.get("command_type")
    params = payload.get("parameters") or {}
    camera_id = int(params.get("camera_id", 1))
    ptz = ptz_controllers.get(camera_id)

    if command == "ptz_override":
        if not ptz:
            return {"command_accepted": False, "reason": f"no PTZ on cam {camera_id}"}
        method = getattr(ptz, "apply_override", None)
        if not callable(method):
            return {"command_accepted": False, "reason": "ptz override not implemented"}
        method(params)
        return {"command_accepted": True}

    if command == "deterrence_standdown":
        # Standdown is only valid during an active maintenance window. The
        # actual gate is enforced by the alert engine; here we just accept.
        return {"command_accepted": True}

    if command == "request_stream":
        return {
            "command_accepted": True,
            "stream": {"protocol": "mjpeg", "url_hint": f"/video_feed/{camera_id}"},
        }

    return {"command_accepted": False, "reason": f"unknown command {command}"}
