"""Event-driven camera worker — the lean alternative to CameraWorker.

Designed for Jetson Orin Nano (8 GB unified RAM) where the streaming
CameraWorker's footprint (4×1920px NVDEC decode + 15s pre-event buffer +
24/7 inference loop) exceeds available memory.

Lifecycle:

  1. Subscribe to the camera's Dahua eventManager.cgi stream.
  2. Sleep until a `Start` event fires (VideoMotion / CrossLineDetection /
     CrossRegionDetection / ObjectDetect).
  3. Fetch a single JPEG snapshot via `/cgi-bin/snapshot.cgi`.
  4. Run YOLO (and face_engine, if enabled) on that one frame.
  5. If anything was detected, push to the AlertEngine.
  6. Cool down for N seconds — never burst-process the same event twice.
  7. Publish the annotated snapshot to FrameBus so /video_feed/<N> shows
     the most-recent inferred frame instead of a blank panel.

Memory profile (measured on Orin Nano, 1 camera):

  * Idle: ~150 MB (just the listener thread + queue)
  * During a burst: +200-400 MB transient (YOLO inference + ndarray copy)
  * No pre-event ring buffer, no GStreamer pipeline, no continuous decode.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

import numpy as np

from config.settings import settings
from itips.camera.dahua_http import DahuaCameraEndpoint
from itips.runtime.camera_worker import WorkerDeps
from itips.runtime.frame_bus import FrameSnapshot
from itips.sensors.dahua_events import DahuaEvent, DahuaEventListener
from itips.utils.clock import monotonic_ns

logger = logging.getLogger(__name__)


# Events that actually warrant inference. `Stop` actions are noise; we only
# care about the rising edge ("Start") and one-shot pulses ("Pulse").
_ACTIONABLE_ACTIONS = ("Start", "Pulse")


class EventDrivenWorker(threading.Thread):
    """Snapshot-based worker that runs inference only on camera events."""

    def __init__(self, camera_id: int, rtsp_url: str, deps: WorkerDeps,
                 cooldown_s: float = 2.0,
                 max_width: int = 1280) -> None:
        super().__init__(name=f"event-cam{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.deps = deps
        self._cooldown_s = cooldown_s
        self._max_width = max_width
        self._last_processed_at = 0.0
        self._stop = threading.Event()
        self._queue: "queue.Queue[DahuaEvent]" = queue.Queue(maxsize=8)

        # We accept the same RTSP URL the streaming worker uses, then derive
        # the HTTP endpoint from it. Keeps `.env` unchanged.
        self._endpoint = DahuaCameraEndpoint.from_rtsp_url(rtsp_url)
        if self._endpoint is None:
            logger.error("cam %d: cannot parse HTTP endpoint from RTSP URL — worker idle",
                         camera_id)
            self._listener = None
        else:
            self._listener = DahuaEventListener(
                endpoint=self._endpoint,
                camera_id=camera_id,
                on_event=self._on_event,
            )

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.stop()

    def run(self) -> None:
        if self._listener is None:
            # Nothing to do — sleep until stop.
            self._stop.wait()
            return
        self._listener.start()
        logger.info("EventDrivenWorker cam %d running — endpoint=%s, cooldown=%.1fs",
                    self.camera_id, self._endpoint.safe_label(), self._cooldown_s)
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process_event(event)
            except Exception:
                logger.exception("cam %d: process_event crashed", self.camera_id)
        logger.info("EventDrivenWorker cam %d stopped", self.camera_id)

    # ---------------------------------------------------------------- internals

    def _on_event(self, event: DahuaEvent) -> None:
        """Called by the listener thread — never block here."""
        if event.action not in _ACTIONABLE_ACTIONS:
            return
        # Cooldown: silently drop events that fire while we're still
        # cooling down from the last one. The queue's maxsize=8 is a
        # secondary safety net.
        now = time.monotonic()
        if now - self._last_processed_at < self._cooldown_s:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.debug("cam %d: event queue full, dropping %s",
                         self.camera_id, event.code)

    def _process_event(self, event: DahuaEvent) -> None:
        """Fetch snapshot + run inference. Single-frame, no tracking."""
        self._last_processed_at = time.monotonic()

        assert self._endpoint is not None  # listener is None otherwise
        frame = self._endpoint.snapshot(timeout=5.0)
        if frame is None:
            return

        frame = self._maybe_downscale(frame)
        logger.info("cam %d EVENT %s/%s -> snapshot %dx%d",
                    self.camera_id, event.code, event.action,
                    frame.shape[1], frame.shape[0])

        try:
            detection_result = self.deps.yolo_engine.detect(
                frame, camera_id=self.camera_id,
            )
        except AttributeError as exc:
            # Same lazy-fuse race the streaming worker handles.
            logger.warning("cam %d: YOLO transient (%s); skipping", self.camera_id, exc)
            return

        face_results = []
        if detection_result.detections:
            try:
                face_results = self.deps.face_engine.recognize(
                    frame, detections=detection_result.detections,
                    camera_id=self.camera_id,
                ) or []
            except Exception:
                logger.exception("cam %d: face_engine.recognize crashed", self.camera_id)

        # Minimal alert path: a Dahua "Start" event from a smart rule
        # already means *the camera* thinks something interesting happened.
        # If YOLO ALSO sees a person, we trust the event and emit an alert.
        # (Full behaviour analysis stays the streaming worker's job — that
        # path needs continuous tracking which we can't afford here.)
        if detection_result.detections:
            self._emit_alert(event, frame, detection_result.detections, face_results)

        # Publish the annotated snapshot so /video_feed/<N> still shows
        # *something* even though we no longer stream continuously.
        annotated = self._annotate(frame, detection_result, face_results)
        # Persist the annotated snapshot to evidence_store ONLY when YOLO
        # actually saw something — keeps the directory clean of camera-side
        # false positives (wind, lighting changes, IR-cut flicker).
        if detection_result.detections or detection_result.vehicles:
            self._persist_snapshot(annotated, event, detection_result)
        self.deps.frame_bus.publish(FrameSnapshot(
            camera_id=self.camera_id,
            raw=frame,
            annotated=annotated,
            monotonic_ns=monotonic_ns(),
            preset_id="default",
        ))

    def _emit_alert(self, event: DahuaEvent, frame: np.ndarray,
                    detections, face_results) -> None:
        """Route to AlertEngine. Reuses the face_intruder path because it's
        the only one that accepts a single-frame, no-track payload today."""
        # Pick the largest person bbox as the alert anchor.
        largest = max(detections, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
        bbox = list(largest.bbox)
        intruder_face = next((fr for fr in face_results if getattr(fr, "name", "") == "INTRUDER"), None)

        handler = (getattr(self.deps.alert_engine, "handle_camera_event", None)
                   or getattr(self.deps.alert_engine, "handle_face_intruder", None))
        if handler is None:
            logger.warning("cam %d: AlertEngine has no compatible handler — alert dropped",
                           self.camera_id)
            return
        try:
            handler(camera_id=self.camera_id,
                    face_bbox=intruder_face.bbox if intruder_face else bbox,
                    name=intruder_face.name if intruder_face else f"motion:{event.code}")
        except TypeError:
            # Older AlertEngine signature — fall back to positional args.
            try:
                handler(self.camera_id, bbox)
            except Exception:
                logger.exception("cam %d: AlertEngine handler crashed", self.camera_id)

    def _persist_snapshot(self, frame: np.ndarray, event: DahuaEvent,
                          detection_result) -> None:
        """Save the annotated frame to evidence_store/snapshots/cam<N>/.

        Filename pattern: {iso-utc}_{event-code}.jpg. UTC keeps filenames
        sortable across timezones; the colon-free format keeps them
        Windows/SMB-safe in case operators copy the dir off-device.
        """
        from datetime import datetime, timezone

        from config.settings import settings as _settings

        out_dir = _settings.evidence.store_path / "snapshots" / f"cam{self.camera_id}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("cam %d: cannot create snapshot dir %s",
                             self.camera_id, out_dir)
            return

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        fname = f"{ts}_{event.code}.jpg"
        path = out_dir / fname

        import cv2
        ok = cv2.imwrite(str(path), frame,
                         [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok:
            logger.info(
                "cam %d snapshot saved: %s (%d persons, %d vehicles)",
                self.camera_id, path,
                len(detection_result.detections),
                len(detection_result.vehicles),
            )
        else:
            logger.warning("cam %d: cv2.imwrite returned False for %s",
                           self.camera_id, path)

    def _maybe_downscale(self, frame: np.ndarray) -> np.ndarray:
        """Resize down if wider than max_width. Tiny CPU cost, big YOLO speedup."""
        h, w = frame.shape[:2]
        if w <= self._max_width:
            return frame
        import cv2
        scale = self._max_width / float(w)
        return cv2.resize(frame, (self._max_width, int(round(h * scale))),
                          interpolation=cv2.INTER_AREA)

    def _annotate(self, frame: np.ndarray, detection_result, face_results) -> np.ndarray:
        """Same overlay routine the streaming worker uses, kept simple."""
        try:
            from itips.utils.drawing import draw_detections, draw_face_results
        except ImportError:
            return frame.copy()
        display = frame.copy()
        if detection_result.detections:
            display = draw_detections(display, detection_result.detections)
        if face_results:
            try:
                display = draw_face_results(display, face_results)
            except Exception:
                pass
        return display
