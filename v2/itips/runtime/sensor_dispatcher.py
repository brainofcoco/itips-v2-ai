"""Sensor → PTZ pan → snapshot → face-validate pipeline.

Single worker thread so a chatty sensor can't saturate the GPU.
Per-zone cooldown debounces; any exception is logged and skipped.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Optional

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
        openai_validator=None,
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
        self._face_engine = face_engine
        self._openai_validator = openai_validator
        # When wired, sensor activations route the captured snapshot into
        # the multi-frame ThreatEvaluator instead of a one-shot face check.
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

        if self._threat_evaluator is not None:
            # Hand the captured frame to the multi-frame evaluator and
            # return immediately — the final verdict is produced later by
            # the evaluator's worker thread.
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
            outcome = {
                "verdict": "evaluating",
                "camera_id": mapping.camera_id,
                "preset_name": mapping.preset_name,
            }
        else:
            outcome = self._validate_face(event=event, mapping=mapping, frame=frame)
        self._event_tap.publish(event, outcome=outcome)

    def _validate_via_llm(self, scenario, frame, context, *,
                          local_confidence, cooldown_key):
        v = self._openai_validator
        if v is None or frame is None:
            return None
        try:
            if not v.should_validate(scenario, local_confidence=local_confidence,
                                     cooldown_key=cooldown_key):
                return None
            return v.validate(scenario, frame, context, cooldown_key=cooldown_key)
        except Exception:
            logger.exception("openai validator crashed for %s", scenario)
            return None

    def _dispatch_escalation(self, validation, mapping) -> None:
        """Auto-route fire / smoke / weapon — bypasses normal sensor flow."""
        common = {"source": "ai_validator",
                  "ai_summary": validation.summary,
                  "ai_confidence": validation.confidence,
                  "camera_id": mapping.camera_id,
                  "preset_name": mapping.preset_name}
        if validation.category == "fire":
            self._alert_engine.handle_fire(camera_id=mapping.camera_id, details=common)
        elif validation.category == "smoke":
            self._alert_engine.handle_smoke(camera_id=mapping.camera_id, details=common)
        elif validation.category == "weapon":
            self._alert_engine.handle_face_intruder(
                camera_id=mapping.camera_id, face_bbox=(0, 0, 0, 0),
                name="WEAPON_INTRUDER",
                details={**common, "severity": "high"},
            )

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
            # No face engine — still ask the LLM what's in the snapshot.
            v = self._validate_via_llm("sensor_unverified", frame, details_common,
                                       local_confidence=0.0,
                                       cooldown_key=("sensor_unverified", event.zone_id))
            if v and v.should_escalate:
                self._dispatch_escalation(v, mapping)
                return {"verdict": f"escalated_{v.category}",
                        "camera_id": mapping.camera_id,
                        "ai_summary": v.summary}
            if v and v.should_suppress:
                return {"verdict": "suppressed_by_ai",
                        "camera_id": mapping.camera_id,
                        "ai_summary": v.summary}
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=mapping.camera_id,
                alert_type="sensor_unverified",
                details={**details_common, "reason": "face engine not wired",
                         **_ai_details_local(v)},
            )
            return {"verdict": "unverified_no_engine",
                    "camera_id": mapping.camera_id,
                    "ai_summary": v.summary if v else None}

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
            # No face in frame — let the LLM identify what tripped the sensor.
            v = self._validate_via_llm("sensor_unverified", frame, details_common,
                                       local_confidence=0.0,
                                       cooldown_key=("sensor_unverified", event.zone_id))
            if v and v.should_escalate:
                self._dispatch_escalation(v, mapping)
                return {"verdict": f"escalated_{v.category}",
                        "camera_id": mapping.camera_id,
                        "ai_summary": v.summary}
            if v and v.should_suppress:
                return {"verdict": "suppressed_by_ai",
                        "camera_id": mapping.camera_id,
                        "ai_summary": v.summary}
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=mapping.camera_id,
                alert_type="sensor_unverified",
                details={**details_common, "reason": "no face detected",
                         **_ai_details_local(v)},
            )
            return {"verdict": "unverified_no_face",
                    "camera_id": mapping.camera_id,
                    "ai_summary": v.summary if v else None}

        if result.matched and result.person_id:
            self._alert_engine.handle_personnel_seen(
                camera_id=mapping.camera_id,
                person_uid=result.person_id,
                group_id="jetson-sensor-validated",
                name=result.full_name or "",
                similarity=int(round(result.similarity * 100)),
                jpeg=_encode_frame_jpeg(frame),
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

        # Face present but no enrolled match → INTRUDER (LLM may downgrade).
        v = self._validate_via_llm("sensor_intruder", frame,
                                   {**details_common,
                                    "confidence": result.similarity},
                                   local_confidence=result.similarity,
                                   cooldown_key=("sensor_intruder", event.zone_id))
        if v and v.should_suppress:
            return {"verdict": "suppressed_by_ai",
                    "camera_id": mapping.camera_id,
                    "ai_summary": v.summary,
                    "similarity": round(float(result.similarity), 4)}
        if v and v.should_escalate:
            self._dispatch_escalation(v, mapping)
            return {"verdict": f"escalated_{v.category}",
                    "camera_id": mapping.camera_id,
                    "ai_summary": v.summary}
        self._alert_engine.handle_face_intruder(
            camera_id=mapping.camera_id,
            face_bbox=(0, 0, 0, 0),
            name="INTRUDER",
            details=_ai_details_local(v),
            jpeg=_encode_frame_jpeg(frame),
        )
        logger.info(
            "sensor zone=%d INTRUDER (best_sim=%.2f) on cam %d%s",
            event.zone_id, result.similarity, mapping.camera_id,
            f" ai={v.category}/{v.verdict}" if v else "",
        )
        return {
            "verdict": "intruder",
            "similarity": round(float(result.similarity), 4),
            "camera_id": mapping.camera_id,
            "ai_summary": v.summary if v else None,
        }


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


def _encode_frame_jpeg(frame) -> Optional[bytes]:
    """JPEG-encode the snapshot for the evidence package."""
    if frame is None:
        return None
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else None
    except Exception:
        logger.exception("sensor frame jpeg encode failed")
        return None


def _ai_details_local(validation) -> dict:
    if validation is None:
        return {}
    return {
        "ai_summary": validation.summary,
        "ai_category": validation.category,
        "ai_verdict": validation.verdict,
        "ai_confidence": round(float(validation.confidence), 3),
        "ai_model": validation.model,
    }
