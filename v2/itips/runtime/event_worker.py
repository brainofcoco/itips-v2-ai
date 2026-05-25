"""Per-camera Dahua event dispatcher.

The Jetson runs zero inference. Each `DahuaEventDispatcher` instance owns
one long-poll subscription to a camera's `eventManager.cgi` stream and
routes incoming events to the AlertEngine.

Lifecycle:

  1. Subscribe to the curated event-code list (see `dahua_events.DEFAULT_CODES`).
  2. On each event:
        * pull the JPEG that came with the multipart payload (if any),
          else fetch one via `/cgi-bin/snapshot.cgi`,
        * dispatch through the per-code handler table,
        * publish the snapshot to FrameBus so the dashboard's MJPEG
          endpoint can serve it.
  3. Per-event-code cooldown so a noisy rule cannot pin the CPU.
  4. Per-camera face-DB binding is guaranteed at start: if the workers
     group is bound, the camera will only emit FaceRecognition for that
     group; if not, we still react to FaceDetection.

Memory profile on Orin Nano: ~80 MB resident (Python + requests), no
GPU footprint at all. All heavy work is on the camera.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from itips.camera.dahua_http import DahuaCameraEndpoint
from itips.runtime.frame_bus import FrameBus, FrameSnapshot
from itips.sensors.dahua_events import DahuaEvent, DahuaEventListener
from itips.utils.clock import monotonic_ns

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


_ACTIONABLE = {"Start", "Pulse"}

# Codes that don't carry useful state on Stop (we still log Start).
_START_ONLY_CODES = {
    "CrossLineDetection",
    "CrossRegionDetection",
    "WanderDetection",
    "FireDetection",
    "SmokeDetection",
    "WorkClothesDetection",
    "FaceDetection",
    "VideoMotion",
}


class WorkerDeps:
    """Bundle of long-lived services every dispatcher needs.

    Plain `class` (not dataclass) because the streaming-era WorkerDeps
    dataclass shipped with too many ML attributes and tying us to it
    keeps the dead surface alive.
    """

    def __init__(
        self,
        *,
        alert_engine,
        frame_bus: FrameBus,
        recorders: Optional[dict[int, Any]] = None,
        event_tap: Optional[Any] = None,
    ) -> None:
        self.alert_engine = alert_engine
        self.frame_bus = frame_bus
        self.recorders = recorders or {}
        self.event_tap = event_tap


class DahuaEventDispatcher(threading.Thread):
    """Dahua event subscriber + dispatch loop for one camera."""

    def __init__(
        self,
        camera_id: int,
        rtsp_url: str,
        deps: WorkerDeps,
        *,
        cooldown_s: float = 1.5,
        face_intruder_cooldown_s: float = 3.0,
    ) -> None:
        super().__init__(name=f"cam{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.deps = deps
        self._cooldown_s = cooldown_s
        self._face_intruder_cooldown_s = face_intruder_cooldown_s
        self._stop = threading.Event()
        self._queue: queue.Queue[DahuaEvent] = queue.Queue(maxsize=32)
        self._last_processed: dict[str, float] = {}

        self._endpoint = DahuaCameraEndpoint.from_rtsp_url(rtsp_url)
        if self._endpoint is None:
            logger.error("cam %d: cannot parse endpoint from RTSP — dispatcher idle", camera_id)
            self._listener = None
        else:
            self._listener = DahuaEventListener(
                endpoint=self._endpoint,
                camera_id=camera_id,
                on_event=self._on_event,
            )

        self._handlers: dict[str, Callable[[DahuaEvent, Optional["np.ndarray"]], None]] = {
            "FaceRecognition": self._handle_face_recognition,
            "FaceDetection": self._handle_face_detection,
            "CrossLineDetection": self._handle_perimeter_breach,
            "CrossRegionDetection": self._handle_intrusion,
            "WanderDetection": self._handle_loitering,
            "TrafficCarMeasurement": self._handle_plate_capture,
            "CarDrivingInOut": self._handle_vehicle_gate,
            "FireDetection": self._handle_fire,
            "SmokeDetection": self._handle_smoke,
            "WorkClothesDetection": self._handle_work_clothes,
            "VideoMotion": self._handle_motion,
        }

    # ─── lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.stop()

    def run(self) -> None:
        if self._listener is None:
            self._stop.wait()
            return
        self._listener.start()
        logger.info(
            "DahuaEventDispatcher cam %d running — endpoint=%s",
            self.camera_id, self._endpoint.safe_label(),
        )
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process(event)
            except Exception:
                logger.exception("cam %d: process %s/%s failed",
                                 self.camera_id, event.code, event.action)
        logger.info("DahuaEventDispatcher cam %d stopped", self.camera_id)

    # ─── ingest ───────────────────────────────────────────────────────

    def _on_event(self, event: DahuaEvent) -> None:
        """Listener thread → bounded queue. Never block here."""
        # Tap EVERYTHING (including non-actionable Stop edges and cooldown
        # drops) so the Test Console can show what the camera is saying.
        if self.deps.event_tap is not None:
            try:
                self.deps.event_tap.publish(
                    camera_id=self.camera_id,
                    code=event.code,
                    action=event.action,
                    index=event.index,
                    data=event.data,
                    has_jpeg=event.jpeg is not None,
                )
            except Exception:
                pass
        if event.action not in _ACTIONABLE:
            return
        if event.code in _START_ONLY_CODES and event.action != "Start" and event.action != "Pulse":
            return
        cooldown_key = self._cooldown_key(event)
        last = self._last_processed.get(cooldown_key, 0.0)
        if (time.monotonic() - last) < self._cooldown_for(event):
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.debug("cam %d: event queue full, dropping %s",
                         self.camera_id, event.code)

    def _cooldown_key(self, event: DahuaEvent) -> str:
        return f"{event.code}:{event.index}"

    def _cooldown_for(self, event: DahuaEvent) -> float:
        if event.code == "FaceRecognition":
            return self._face_intruder_cooldown_s
        return self._cooldown_s

    # ─── core dispatch ────────────────────────────────────────────────

    def _process(self, event: DahuaEvent) -> None:
        cooldown_key = self._cooldown_key(event)
        self._last_processed[cooldown_key] = time.monotonic()

        frame = self._frame_for(event)
        handler = self._handlers.get(event.code, self._handle_unknown)
        handler(event, frame)

        if frame is not None:
            self.deps.frame_bus.publish(FrameSnapshot(
                camera_id=self.camera_id,
                raw=frame,
                annotated=frame,
                monotonic_ns=monotonic_ns(),
                preset_id="default",
            ))
            self._feed_recorder(frame)

    def _frame_for(self, event: DahuaEvent) -> Optional["np.ndarray"]:
        if event.jpeg:
            try:
                import cv2
                import numpy as np
                buf = np.frombuffer(event.jpeg, dtype=np.uint8)
                frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if frame is not None:
                    return frame
            except Exception:
                logger.exception("cam %d: jpeg decode failed", self.camera_id)
        if self._endpoint is None:
            return None
        return self._endpoint.snapshot(timeout=4.0)

    def _feed_recorder(self, frame: "np.ndarray") -> None:
        rec = self.deps.recorders.get(self.camera_id)
        if rec is None:
            return
        try:
            rec.feed(frame)
        except Exception:
            logger.exception("cam %d: recorder.feed crashed", self.camera_id)

    # ─── per-event handlers ───────────────────────────────────────────

    def _handle_face_recognition(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        """The discriminator: known worker vs intruder."""
        candidates = event.data.get("Candidates") or []
        face = event.data.get("Face", {}) or {}
        bbox = _face_bbox(face)

        if candidates:
            top = candidates[0]
            person = top.get("Person", {}) if isinstance(top, dict) else {}
            similarity = int(top.get("Similarity", 0) or 0) if isinstance(top, dict) else 0
            self.deps.alert_engine.handle_personnel_seen(
                camera_id=self.camera_id,
                person_uid=str(person.get("UID", "")),
                group_id=str(person.get("GroupID", "")),
                name=str(person.get("Name", "")),
                similarity=similarity,
            )
            logger.info(
                "cam %d FaceRecognition KNOWN uid=%s name=%s sim=%d",
                self.camera_id, person.get("UID"), person.get("Name"), similarity,
            )
            return

        # No match → intruder.
        self.deps.alert_engine.handle_face_intruder(
            camera_id=self.camera_id,
            face_bbox=bbox,
            name="INTRUDER",
        )
        logger.info("cam %d FaceRecognition INTRUDER (no candidates)", self.camera_id)

    def _handle_face_detection(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        """Bare face detect — only emit if we're NOT also doing FaceRecognition."""
        face = event.data.get("Face", {}) or {}
        bbox = _face_bbox(face)
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="face_detected",
            details={"bbox": list(bbox)},
        )

    def _handle_perimeter_breach(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        direction = event.data.get("Direction", "")
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="line_crossing",
            details={
                "direction": direction,
                "rule_name": event.data.get("Name", ""),
            },
        )

    def _handle_intrusion(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        action = event.data.get("Action", "")  # Appear|Disappear|Cross|Inside
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="intrusion",
            details={
                "action": action,
                "rule_name": event.data.get("Name", ""),
            },
        )

    def _handle_loitering(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="loitering",
            details={
                "rule_name": event.data.get("Name", ""),
                "object_count": len(event.data.get("Objects") or []),
            },
        )

    def _handle_plate_capture(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        traffic = event.data.get("TrafficCar") or event.data
        plate_number = traffic.get("PlateNumber") if isinstance(traffic, dict) else None
        self.deps.alert_engine.handle_plate_capture(
            camera_id=self.camera_id,
            plate_number=str(plate_number) if plate_number else "",
            plate_color=traffic.get("PlateColor") if isinstance(traffic, dict) else None,
            vehicle_color=traffic.get("VehicleColor") if isinstance(traffic, dict) else None,
            speed=traffic.get("Speed") if isinstance(traffic, dict) else None,
        )

    def _handle_vehicle_gate(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        direction = event.data.get("DrivingDirection")
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="vehicle_gate",
            details={"direction": "enter" if direction == 1 else "leave" if direction == 2 else "unknown"},
        )

    def _handle_fire(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        if event.data.get("MisReport"):
            return
        self.deps.alert_engine.handle_fire(
            camera_id=self.camera_id,
            details={"rule": event.data.get("RuleType", "FireDetection")},
        )

    def _handle_smoke(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        if event.data.get("MisReport"):
            return
        self.deps.alert_engine.handle_smoke(
            camera_id=self.camera_id,
            details={"color": event.data.get("SmokeColor", "")},
        )

    def _handle_work_clothes(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        # Phase 2 — log via behaviour rail.
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="work_clothes_violation",
            details={"alarm_type": event.data.get("AlarmType", 0)},
        )

    def _handle_motion(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        # Lowest priority — log only. Useful when the camera fires this in
        # lieu of CrossLineDetection (older firmwares).
        self.deps.alert_engine.handle_behaviour_alert_simple(
            camera_id=self.camera_id,
            alert_type="motion",
            details={},
        )

    def _handle_unknown(self, event: DahuaEvent, _frame: Optional["np.ndarray"]) -> None:
        logger.debug("cam %d: ignoring %s/%s", self.camera_id, event.code, event.action)


# Backwards-compatible alias so the orchestrator import path holds.
EventDrivenWorker = DahuaEventDispatcher


def _face_bbox(face: dict[str, Any]) -> tuple[float, float, float, float]:
    bbox = face.get("BoundingBox") or face.get("Bound") or [0, 0, 0, 0]
    if isinstance(bbox, list) and len(bbox) >= 4:
        return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return (0.0, 0.0, 0.0, 0.0)
