"""Sensor → PTZ pan → snapshot → face-validate pipeline.

Single worker thread so a chatty sensor can't saturate the GPU.
Per-zone cooldown debounces; any exception is logged and skipped.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Optional

from itips.sensors.sensor_event import SensorEvent, SensorEventTap
from itips.sensors.sensor_map import SensorMap

logger = logging.getLogger(__name__)


class SensorDispatcher(threading.Thread):
    """Turns SensorEvents into PTZ + snapshot + face-validate alerts."""

    def __init__(
        self,
        *,
        alert_engine,
        dahua_manager,
        sensor_map: SensorMap,
        event_tap: SensorEventTap,
        face_engine=None,
        pan_settle_s: float = 2.0,
        snapshot_timeout_s: float = 4.0,
        per_zone_cooldown_s: float = 10.0,
        queue_size: int = 64,
    ) -> None:
        super().__init__(name="sensor-dispatcher", daemon=True)
        self._alert_engine = alert_engine
        self._dahua_manager = dahua_manager
        self._sensor_map = sensor_map
        self._event_tap = event_tap
        self._face_engine = face_engine
        self._pan_settle_s = float(pan_settle_s)
        self._snapshot_timeout_s = float(snapshot_timeout_s)
        self._per_zone_cooldown_s = float(per_zone_cooldown_s)
        self._queue: queue.Queue[SensorEvent] = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._last_dispatch: dict[int, float] = {}

    def start(self) -> None:
        # Idempotent — orchestrator's start-all loop may call twice.
        if self.is_alive():
            return
        super().start()

    def stop(self) -> None:
        self._stop_event.set()

    def dispatch(self, event: SensorEvent) -> bool:
        """Returns False if the queue is full — drop rather than block."""
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            logger.warning(
                "sensor queue full — dropping zone=%d type=%s",
                event.zone_id, event.event_type,
            )
            return False

    def run(self) -> None:
        logger.info("SensorDispatcher running (pan_settle=%.1fs cooldown=%.1fs)",
                    self._pan_settle_s, self._per_zone_cooldown_s)
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process(event)
            except Exception:
                logger.exception("sensor process failed for zone=%d", event.zone_id)
        logger.info("SensorDispatcher stopped")

    def _process(self, event: SensorEvent) -> None:
        # Always log the raw alarm — audit trail even when the rest fails.
        self._alert_engine.handle_behaviour_alert_simple(
            camera_id=0,
            alert_type="sensor_alarm",
            details={
                "zone_id": event.zone_id,
                "zone_name": event.zone_name,
                "sensor_type": event.event_type,
                "event_state": event.event_state,
                "source": event.source,
            },
        )

        # Per-zone cooldown — chatty sensor must not slew the PTZ continuously.
        now = time.monotonic()
        last = self._last_dispatch.get(event.zone_id, 0.0)
        if (now - last) < self._per_zone_cooldown_s:
            outcome = {"verdict": "cooldown",
                       "remaining_s": round(self._per_zone_cooldown_s - (now - last), 2)}
            self._event_tap.publish(event, outcome=outcome)
            logger.info("sensor zone=%d in cooldown, skipping", event.zone_id)
            return
        self._last_dispatch[event.zone_id] = now

        mapping = self._sensor_map.get(event.zone_id)
        if mapping is None:
            outcome = {"verdict": "unmapped"}
            self._event_tap.publish(event, outcome=outcome)
            logger.info("sensor zone=%d has no map entry — alarm only", event.zone_id)
            return

        client = self._dahua_manager.get(mapping.camera_id)
        if client is None:
            outcome = {"verdict": "no_camera", "camera_id": mapping.camera_id}
            self._event_tap.publish(event, outcome=outcome)
            logger.warning("sensor zone=%d maps to unknown cam %d",
                           event.zone_id, mapping.camera_id)
            return

        pan_ok = False
        try:
            pan_ok = client.ptz.goto_preset_by_name(mapping.preset_name)
        except Exception:
            logger.exception("sensor zone=%d: PTZ pan crashed", event.zone_id)
        if not pan_ok:
            self._event_tap.publish(event, outcome={
                "verdict": "pan_failed",
                "camera_id": mapping.camera_id,
                "preset_name": mapping.preset_name,
            })
            return

        # GotoPreset is fire-and-forget on Dahua; sleep for the dome to arrive.
        time.sleep(self._pan_settle_s)

        frame = None
        try:
            frame = client.endpoint.snapshot(timeout=self._snapshot_timeout_s)
        except Exception:
            logger.exception("sensor zone=%d: snapshot failed", event.zone_id)
        if frame is None:
            self._event_tap.publish(event, outcome={
                "verdict": "snapshot_failed",
                "camera_id": mapping.camera_id,
                "preset_name": mapping.preset_name,
            })
            return

        outcome = self._validate_face(event=event, mapping=mapping, frame=frame)
        self._event_tap.publish(event, outcome=outcome)

    def _validate_face(
        self,
        *,
        event: SensorEvent,
        mapping,
        frame,
    ) -> dict[str, Any]:
        """Routes through the same handlers a camera-triggered face match uses."""
        details_common = {
            "trigger": "sensor",
            "zone_id": event.zone_id,
            "zone_name": event.zone_name,
            "sensor_type": event.event_type,
            "camera_id": mapping.camera_id,
            "preset_name": mapping.preset_name,
        }

        if self._face_engine is None:
            # No engine — log presence + leave the snapshot for operator review.
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=mapping.camera_id,
                alert_type="sensor_unverified",
                details={**details_common, "reason": "face engine not wired"},
            )
            return {"verdict": "unverified_no_engine",
                    "camera_id": mapping.camera_id}

        try:
            result = self._face_engine.recognize(frame, bbox=None)
        except Exception:
            logger.exception("sensor zone=%d: face engine crashed", event.zone_id)
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=mapping.camera_id,
                alert_type="sensor_unverified",
                details={**details_common, "reason": "face engine error"},
            )
            return {"verdict": "engine_error",
                    "camera_id": mapping.camera_id}

        if result.embedding is None:
            # No face in frame — person moved out of view, animal, false alarm.
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=mapping.camera_id,
                alert_type="sensor_unverified",
                details={**details_common, "reason": "no face detected"},
            )
            return {"verdict": "unverified_no_face",
                    "camera_id": mapping.camera_id}

        if result.matched and result.person_id:
            self._alert_engine.handle_personnel_seen(
                camera_id=mapping.camera_id,
                person_uid=result.person_id,
                group_id="jetson-sensor-validated",
                name=result.full_name or "",
                similarity=int(round(result.similarity * 100)),
            )
            logger.info(
                "sensor zone=%d AUTHORISED — %s (sim=%.2f) on cam %d",
                event.zone_id, result.full_name, result.similarity,
                mapping.camera_id,
            )
            return {
                "verdict": "authorised",
                "person_id": result.person_id,
                "full_name": result.full_name,
                "similarity": round(float(result.similarity), 4),
                "camera_id": mapping.camera_id,
            }

        # Face present but no enrolled match → intruder.
        self._alert_engine.handle_face_intruder(
            camera_id=mapping.camera_id,
            face_bbox=(0, 0, 0, 0),
            name="INTRUDER",
        )
        logger.info(
            "sensor zone=%d INTRUDER (best_sim=%.2f) on cam %d",
            event.zone_id, result.similarity, mapping.camera_id,
        )
        return {
            "verdict": "intruder",
            "similarity": round(float(result.similarity), 4),
            "camera_id": mapping.camera_id,
        }
