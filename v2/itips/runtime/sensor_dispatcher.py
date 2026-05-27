"""Sensor → PTZ pan → snapshot → ThreatEvaluator pipeline.

Single worker thread so a chatty sensor can't saturate the GPU.
Per-zone cooldown debounces; any exception is logged and skipped.

The dispatcher's job is mechanical — pan the dome, grab a frame, hand it
off to the ThreatEvaluator. The evaluator runs the multi-frame decision
window that produces the AUTHORIZED / INTRUDER / UNCERTAIN verdict.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from itips.sensors.sensor_event import SensorEvent, SensorEventTap
from itips.sensors.sensor_map import SensorMap

logger = logging.getLogger(__name__)


class SensorDispatcher(threading.Thread):
    """Turns SensorEvents into PTZ + snapshot + evaluator triggers."""

    def __init__(
        self,
        *,
        alert_engine,
        dahua_manager,
        sensor_map: SensorMap,
        event_tap: SensorEventTap,
        threat_evaluator=None,
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
        # When None, the dispatcher still pans + snapshots (operator has
        # evidence to review) but cannot produce a verdict. Production
        # boots the evaluator in app.py whenever the face engine loads.
        self._threat_evaluator = threat_evaluator
        self._pan_settle_s = float(pan_settle_s)
        self._snapshot_timeout_s = float(snapshot_timeout_s)
        self._per_zone_cooldown_s = float(per_zone_cooldown_s)
        self._queue: queue.Queue[SensorEvent] = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._last_dispatch: dict[int, float] = {}
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []

    def add_event_listener(
        self, listener: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to raw sensor activations. Fires once per accepted
        event before the per-zone cooldown skips the PTZ slew, so
        downstream alarm panels still hear every trigger."""
        self._event_listeners.append(listener)

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
        # Fan out the raw activation to any webhook / automation
        # listeners before the cooldown gate, so a chatty zone still
        # surfaces every trigger downstream.
        self._notify_listeners(event)
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

        if self._threat_evaluator is None:
            # No evaluator wired (face engine missing). Log so operators
            # know why no verdict will arrive.
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=mapping.camera_id,
                alert_type="sensor_unverified",
                details={
                    "trigger": "sensor",
                    "zone_id": event.zone_id,
                    "zone_name": event.zone_name,
                    "sensor_type": event.event_type,
                    "preset_name": mapping.preset_name,
                    "reason": "threat evaluator not wired",
                },
            )
            self._event_tap.publish(event, outcome={
                "verdict": "unverified_no_engine",
                "camera_id": mapping.camera_id,
                "preset_name": mapping.preset_name,
            })
            return

        # Hand the captured frame to the multi-frame evaluator and return
        # immediately — the final verdict is produced later by the
        # evaluator's worker thread and published as incident.verdict.
        self._threat_evaluator.trigger(
            camera_id=mapping.camera_id,
            trigger_kind=f"sensor:zone-{event.zone_id}",
            initial_frame=frame,
            details={
                "zone_id": event.zone_id,
                "zone_name": event.zone_name,
                "sensor_type": event.event_type,
                "preset_name": mapping.preset_name,
            },
        )
        self._event_tap.publish(event, outcome={
            "verdict": "evaluating",
            "camera_id": mapping.camera_id,
            "preset_name": mapping.preset_name,
        })

    def _notify_listeners(self, event: SensorEvent) -> None:
        if not self._event_listeners:
            return
        payload = event.to_dict()
        for listener in self._event_listeners:
            try:
                listener(payload)
            except Exception:
                logger.exception("sensor event listener failed (zone=%d)",
                                 event.zone_id)
