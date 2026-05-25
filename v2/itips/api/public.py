"""Port 5050 public API — live MJPEG feeds, SSE alerts, evidence reads.

This is the dashboard-facing surface during POC. WebRTC replacement is a
Phase 1 deliverable.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from flask import Flask, Response, jsonify, stream_with_context

from config.settings import settings
from itips.api.personnel_store import PersonnelStore
from itips.camera.dahua_manager import DahuaManager
from itips.runtime.frame_bus import FrameBus

logger = logging.getLogger(__name__)


class PublicApiServer(threading.Thread):
    def __init__(
        self,
        *,
        frame_bus: FrameBus,
        alert_engine,
        dahua_manager: DahuaManager,
        personnel_store: PersonnelStore,
        event_tap=None,
        capability_router=None,
        face_engine=None,
        plate_engine=None,
        behavior_engine=None,
        zone_store=None,
    ) -> None:
        super().__init__(name="api-public", daemon=True)
        self._frame_bus = frame_bus
        self._alert_engine = alert_engine
        self._dahua = dahua_manager
        self._personnel = personnel_store
        self._event_tap = event_tap
        self._capability_router = capability_router
        self._face_engine = face_engine
        self._plate_engine = plate_engine
        self._behavior_engine = behavior_engine
        self._zone_store = zone_store
        self._app = self._build_app()
        self._server = None

    def _build_app(self) -> Flask:
        from itips.api.dashboard import register_dashboard
        from itips.api.docs import register_docs

        app = Flask("itips-public")
        register_docs(app)
        register_dashboard(
            app,
            frame_bus=self._frame_bus,
            dahua_manager=self._dahua,
            personnel_store=self._personnel,
            alert_engine=self._alert_engine,
            event_tap=self._event_tap,
            capability_router=self._capability_router,
            face_engine=self._face_engine,
            plate_engine=self._plate_engine,
            behavior_engine=self._behavior_engine,
            zone_store=self._zone_store,
        )

        @app.get("/health")
        def health():
            return jsonify({"status": "ok"})

        @app.get("/status")
        def status():
            return jsonify({
                "version": "2.1.0",
                "mode": settings.mode,
                "site_id": settings.tenant.site_id or None,
                "operator_id": settings.tenant.operator_id or None,
                "device_id": settings.tenant.device_id or None,
                "active_cameras": self._dahua.camera_ids(),
                "frame_bus_cameras": self._frame_bus.active_cameras(),
            })

        @app.get("/video_feed/<int:camera_id>")
        def video_feed(camera_id: int):
            return Response(
                stream_with_context(self._mjpeg_generator(camera_id)),
                mimetype="multipart/x-mixed-replace; boundary=frame",
            )

        @app.get("/api/alerts/stream")
        def alerts_stream():
            return Response(
                stream_with_context(self._sse_generator()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.get("/api/alerts/latest")
        def alerts_latest():
            return jsonify(self._alert_engine.history())

        return app

    def run(self) -> None:
        from werkzeug.serving import make_server

        self._server = make_server(
            host=settings.api.public_host,
            port=settings.api.public_port,
            app=self._app,
            threaded=True,
        )
        logger.info("Public API listening on http://%s:%d",
                    settings.api.public_host, settings.api.public_port)
        try:
            self._server.serve_forever()
        except Exception:
            logger.exception("Public API crashed.")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()

    # ─── streamers ─────────────────────────────────────────────────

    def _mjpeg_generator(self, camera_id: int):
        import cv2
        last_ns = -1
        while True:
            snap = self._frame_bus.latest(camera_id)
            if snap is None or snap.annotated is None or snap.monotonic_ns == last_ns:
                time.sleep(0.25)
                continue
            last_ns = snap.monotonic_ns
            ok, encoded = cv2.imencode(".jpg", snap.annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n"
            )
            time.sleep(0.25)

    def _sse_generator(self):
        cursor = 0
        last_send = time.monotonic()
        # Initial tick keeps even silent sites from looking dead — the
        # browser EventSource flips to `open` only after the first byte
        # arrives. A comment line is ignored by client code but counts
        # as a successful read so onopen fires immediately.
        yield ": connected\n\n"
        while True:
            history = self._alert_engine.history()
            new_items = history[cursor:]
            cursor = len(history)
            if new_items:
                for item in new_items:
                    yield f"data: {json.dumps(item)}\n\n"
                last_send = time.monotonic()
            elif (time.monotonic() - last_send) > 15:
                # Heartbeat — without this, a quiet site lets the
                # connection idle long enough for browsers / intermediate
                # proxies to drop it, and the Alerts tab loops on
                # "Reconnecting".
                yield ": keepalive\n\n"
                last_send = time.monotonic()
            time.sleep(0.5)
