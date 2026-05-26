"""Sensor → PTZ-pan → snapshot → face-validate pipeline.

When a sensor alarm reaches the dispatcher (from the AX PRO hub
listener in Phase 2, or from the dashboard's `simulate` button in
Phase 1), it does the following:

  1. Log the raw sensor alarm via `AlertEngine.handle_behaviour_alert_simple`
     so it shows up in the Alerts tab as `sensor_alarm` with the zone
     and sensor type — operators see the trigger even if the rest of
     the pipeline goes nowhere.
  2. Look up the zone in `SensorMap`. If unmapped, the dispatch ends
     here (logged as `outcome=unmapped`). Operators map zones from the
     Sensors tab.
  3. Resolve the target camera + preset. Pan via
     `DahuaPTZ.goto_preset_by_name`, wait `pan_settle_s` for the dome
     to physically arrive.
  4. Pull a snapshot from `snapshot.cgi` on the target camera.
  5. Run `FaceEngine.recognize` against the snapshot. Three branches:
        * matched     → `handle_personnel_seen`  (authorised entry)
        * not matched → `handle_face_intruder`   (unauthorised, escalate)
        * no face     → `handle_behaviour_alert_simple` with
                        `alert_type=sensor_unverified` (sensor fired
                        but the camera couldn't see a person — could
                        be small animal, false alarm, or attacker
                        already moved out of frame)

All work happens on a single worker thread so a noisy sensor can't
saturate the GPU with concurrent face inferences — events queue up
and drain in order with a per-zone cooldown. Crash-isolated like the
other dispatchers: any exception is logged and skipped, the next
event proceeds.
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
    """Single worker that turns SensorEvents into PTZ actions + alerts."""

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
        # zone_id → monotonic ts of last accepted dispatch — keyed
        # separately from the alert engine's idle window so a hammered
        # door sensor can't drown out a real concurrent perimeter alarm.
        self._last_dispatch: dict[int, float] = {}

    # ─── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        # threading.Thread.start may not be re-entrant; mirror the
        # service-shim pattern in the rest of the codebase (idempotent
        # start is expected from the orchestrator's start-all loop).
        if self.is_alive():
            return
        super().start()

    def stop(self) -> None:
        self._stop_event.set()

    # ─── ingest ───────────────────────────────────────────────────────

    def dispatch(self, event: SensorEvent) -> bool:
        """Enqueue. Returns False if the queue is full (drop, don't block)."""
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            logger.warning(
                "sensor queue full — dropping zone=%d type=%s",
                event.zone_id, event.event_type,
            )
            return False

    # ─── worker loop ──────────────────────────────────────────────────

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
        # Always record the raw alarm — gives operators an audit trail
        # even if the rest of the pipeline is unmapped or fails.
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

        # Per-zone cooldown — protects PTZ from being slewed back and
        # forth by a chatty contact sensor.
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

        # Step 1 — pan.
        pan_ok = False
        try:
            pan_ok = client.ptz.goto_preset_by_name(mapping.preset_name)
        except Exception:
            logger.exception("sensor zone=%d: PTZ pan crashed", event.zone_id)
        if not pan_ok:
            outcome = {"verdict": "pan_failed",
                       "camera_id": mapping.camera_id,
                       "preset_name": mapping.preset_name}
            self._event_tap.publish(event, outcome=outcome)
            return

        # Step 2 — wait for the dome to physically arrive. The Dahua
        # ptz.cgi GotoPreset call is fire-and-forget; the camera
        # itself takes 1–2s. Sleeping here is fine — this thread is
        # dedicated to sensor flow.
        time.sleep(self._pan_settle_s)

        # Step 3 — pull a snapshot at the new position.
        frame = None
        try:
            frame = client.endpoint.snapshot(timeout=self._snapshot_timeout_s)
        except Exception:
            logger.exception("sensor zone=%d: snapshot failed", event.zone_id)
        if frame is None:
            outcome = {"verdict": "snapshot_failed",
                       "camera_id": mapping.camera_id,
                       "preset_name": mapping.preset_name}
            self._event_tap.publish(event, outcome=outcome)
            return

        # Step 4 — validate.
        outcome = self._validate_face(
            event=event, mapping=mapping, frame=frame,
        )
        self._event_tap.publish(event, outcome=outcome)

    # ─── face validation ──────────────────────────────────────────────

    def _validate_face(
        self,
        *,
        event: SensorEvent,
        mapping,
        frame,
    ) -> dict[str, Any]:
        """Run the Jetson FaceEngine on the just-captured snapshot.

        Three outcomes routed through the existing alert handlers so
        the Alerts tab and incident lifecycle behave identically to
        camera-triggered face matches.
        """
        details_common = {
            "trigger": "sensor",
            "zone_id": event.zone_id,
            "zone_name": event.zone_name,
            "sensor_type": event.event_type,
            "camera_id": mapping.camera_id,
            "preset_name": mapping.preset_name,
        }

        if self._face_engine is None:
            # No face engine wired — register a generic "person on
            # camera" advisory and let the operator review the snapshot.
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
            # No face in the frame at all. Either the person moved
            # out of view between sensor trip and pan-settle, or the
            # sensor fired on a non-human signal (vibration on a fence
            # rattled by wind, a cat tripping a PIR, …). Either way,
            # we log "sensor fired but no person visible" so the
            # operator can review the snapshot.
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

        # Found a face but no match — INTRUDER.
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
