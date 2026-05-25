"""Per-camera inference worker.

This is the centrepiece of V2's lag fix. Each camera runs in its own
thread with its own behaviour analyser and its own ByteTrack instance,
but shares the YOLO and InsightFace engines.

Workers never call cloud APIs and never write to disk directly — they
hand structured records to the alert engine, which routes everything
through the Sync Agent intake.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from config.settings import settings
from itips.camera.rtsp_reader import RTSPReader
from itips.runtime.frame_bus import FrameBus, FrameSnapshot
from itips.utils.clock import monotonic_ns, now_iso

logger = logging.getLogger(__name__)


@dataclass
class WorkerDeps:
    """Everything a CameraWorker needs from the outside, injected at construction.

    Using a dataclass keeps the constructor signature stable as the pipeline
    grows. Components are typed loosely (Any) here to avoid an import cycle
    with the detection package, which imports utils via the same path.
    """

    yolo_engine: object
    face_engine: object
    face_authorizer: object
    plate_recognizer: Optional[object]
    behaviour_analyser_factory: callable
    alert_engine: object
    ptz_controller: Optional[object]
    frame_bus: FrameBus
    preset_registry: Optional[object] = None


class CameraWorker(threading.Thread):
    """Reads frames from one camera, runs inference, emits alerts."""

    def __init__(self, camera_id: int, rtsp_url: str, deps: WorkerDeps) -> None:
        super().__init__(name=f"cam{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.deps = deps
        self._reader = RTSPReader(rtsp_url, camera_id, settings.cameras.max_frame_width)
        self._analyser = deps.behaviour_analyser_factory(camera_id)
        self._stop = threading.Event()
        self._loop_period_s = 0.0  # uncapped — GPU is the throttle
        self._registrator = self._build_registrator()

    def stop(self) -> None:
        self._stop.set()
        self._reader.stop()

    def run(self) -> None:
        self._reader.start()
        logger.info("CameraWorker %d running — %s", self.camera_id, self._safe_url())

        while not self._stop.is_set():
            frame = self._reader.frame
            if frame is None:
                time.sleep(0.05)
                continue
            try:
                self._process_frame(frame)
            except Exception:
                logger.exception("CameraWorker %d: frame processing failed", self.camera_id)
                time.sleep(0.05)
            if self._loop_period_s:
                time.sleep(self._loop_period_s)

        logger.info("CameraWorker %d stopped", self.camera_id)

    def _process_frame(self, frame) -> None:
        preset_id = self._apply_active_preset()
        frame = self._virtual_ptz(frame)

        # Feed the recorder unconditionally so the pre-event buffer is
        # populated long before any alert opens an incident.
        feed = getattr(self.deps.alert_engine, "feed_frame", None)
        if callable(feed):
            feed(self.camera_id, frame)

        try:
            detection_result = self.deps.yolo_engine.detect(frame, camera_id=self.camera_id)
        except AttributeError as exc:
            # Ultralytics' lazy fuse() can race between workers despite the
            # warmup in YOLOEngine. If it does, log and skip this frame
            # rather than killing the worker thread.
            logger.warning("CameraWorker %d: YOLO transient error (%s); skipping frame",
                           self.camera_id, exc)
            return

        live_zones = self._world_anchor_zones(frame, preset_id)

        events = self._analyser.update(
            detections=detection_result.detections,
            frame_shape=frame.shape,
            camera_id=self.camera_id,
            frame=frame,
            vehicle_detections=detection_result.vehicles,
            preset_id=preset_id,
            zones=live_zones,
        )

        if events:
            for event in events:
                self.deps.alert_engine.handle_behaviour_alert(event)

        # Face recognition is gated on having people in frame to keep the
        # GPU free when nothing is happening — most frames at a quiet site.
        if detection_result.detections:
            face_results = self.deps.face_engine.recognize(
                frame, detections=detection_result.detections, camera_id=self.camera_id
            )
            for fr in face_results or []:
                if getattr(fr, "name", None) == "INTRUDER":
                    self.deps.alert_engine.handle_face_intruder(
                        camera_id=self.camera_id,
                        face_bbox=fr.bbox,
                        name=getattr(fr, "name", "INTRUDER"),
                    )

        annotated = self._draw_overlay(frame, detection_result, preset_id, live_zones)
        self.deps.frame_bus.publish(FrameSnapshot(
            camera_id=self.camera_id,
            raw=frame,
            annotated=annotated,
            monotonic_ns=monotonic_ns(),
            preset_id=preset_id,
        ))

    def _apply_active_preset(self) -> str:
        registry = self.deps.preset_registry
        if registry is None:
            return "default"
        return registry.active(self.camera_id)

    def _virtual_ptz(self, frame):
        """Apply digital crop for sim-PTZ cameras.

        No-op when no preset registry is wired, when the camera has no
        preset definitions, or when a real PTZ controller is connected
        (the physical optics already moved the view).
        """
        registry = self.deps.preset_registry
        if registry is None or not registry.has_definitions(self.camera_id):
            return frame
        ptz = self.deps.ptz_controller
        if ptz is not None and getattr(ptz, "is_connected", False):
            return frame
        from itips.camera.virtual_ptz import apply
        return apply(frame, registry.params(self.camera_id))

    def _draw_overlay(self, frame, detection_result, preset_id: str, zones):
        """Render boxes + zones onto a copy of the frame.

        Kept thin — the drawing functions live in itips.utils.drawing.
        """
        from itips.utils.drawing import draw_detections, draw_zones

        display = frame.copy()
        display = draw_zones(display, camera_id=self.camera_id, preset_id=preset_id, zones=zones)
        if detection_result.detections:
            display = draw_detections(display, detection_result.detections)
        return display

    def _build_registrator(self):
        try:
            from config.settings import settings as _settings
            from itips.behaviour.registration import FrameRegistrator
        except Exception:  # cv2 missing in some test envs
            return None
        root = _settings.zones.runtime_path.parent / "references"
        return FrameRegistrator(camera_id=self.camera_id, references_root=root)

    def _world_anchor_zones(self, frame, preset_id: str):
        """Return per-frame world-anchored zones, or None to fall back.

        Looks up the raw zones from the store, asks the registrator for a
        homography reference→current, and re-projects each polygon. If no
        reference exists or registration fails, returns the raw zones so
        the analyser still sees something.
        """
        from itips.behaviour.zones import get_store as _get_store

        raw = _get_store().for_camera_preset(self.camera_id, preset_id)
        if not raw or self._registrator is None:
            return raw or {}
        try:
            H = self._registrator.compute_homography(frame, preset_id)
        except Exception:
            logger.exception("CameraWorker %d: registrator threw", self.camera_id)
            return raw
        if H is None:
            return raw
        from itips.behaviour.registration import transform_zones
        return transform_zones(raw, H)

    def _safe_url(self) -> str:
        """Redact credentials so they never appear in logs."""
        url = self.rtsp_url or ""
        if "@" in url:
            scheme, rest = url.split("://", 1)
            return f"{scheme}://***@{rest.split('@', 1)[1]}"
        return url
