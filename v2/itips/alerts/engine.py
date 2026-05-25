"""Two-stage alert engine: PRELIMINARY → CONFIRMED.

In the Dahua-driven architecture the camera onboard AI does *every*
primary classification (face match, perimeter line cross, region entry,
loitering, plate read, fire, smoke). The Jetson's job here is just to
turn those classified events into incidents:

  * one PRELIMINARY incident per (camera, recent window),
  * promote to CONFIRMED on a corroborating signal,
  * finalize idle incidents and ship the signed package via the intake.

Confirmation signals:
  - dwell:     same camera keeps emitting events past `confirmation_dwell_seconds`
  - face:      a FaceRecognition with empty Candidates labelled the track INTRUDER
  - fire/smoke: forced fast promotion (life safety)

Everything goes through the intake queue. We never call the cloud here.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from itips.sync.intake import IntakeWriter
from itips.sync.schema import Priority
from itips.utils.clock import monotonic_ns, now_iso

logger = logging.getLogger(__name__)

_MAX_HISTORY = 200

STAGE_PRELIMINARY = "preliminary"
STAGE_CONFIRMED = "confirmed"


@dataclass
class _IncidentState:
    incident_id: str
    camera_id: int
    stage: str
    first_event_at: float
    last_event_at: float
    package_dir: Optional[object] = None
    dwell_required_s: float = 5.0
    confirmation_signals: set[str] = field(default_factory=set)


class AlertEngine:
    def __init__(
        self,
        *,
        intake: IntakeWriter,
        evidence_packager,
        tenant,
        recorders: Optional[dict[int, object]] = None,
        confirmation_dwell_seconds: float = 5.0,
        confirmation_window_seconds: float = 30.0,
        idle_timeout_seconds: float = 15.0,
    ) -> None:
        self._intake = intake
        self._packager = evidence_packager
        self._tenant = tenant
        self._recorders: dict[int, object] = recorders or {}
        self._dwell_s = confirmation_dwell_seconds
        self._window_s = confirmation_window_seconds
        self._idle_s = idle_timeout_seconds
        self._history: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
        self._history_lock = threading.Lock()
        self._incidents: dict[int, _IncidentState] = {}
        self._lock = threading.Lock()
        self._janitor_stop = threading.Event()
        self._janitor: Optional[threading.Thread] = None

    # ─── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if self._janitor is not None:
            return
        self._janitor_stop.clear()
        self._janitor = threading.Thread(
            target=self._janitor_loop, name="alert-janitor", daemon=True
        )
        self._janitor.start()

    def register_recorder(self, camera_id: int, recorder: object) -> None:
        self._recorders[camera_id] = recorder

    def feed_frame(self, camera_id: int, frame) -> None:
        rec = self._recorders.get(camera_id)
        if rec is not None:
            try:
                rec.feed(frame)
            except Exception:
                logger.exception("cam%d: recorder.feed failed", camera_id)

    def stop(self) -> None:
        self._janitor_stop.set()

    # ─── public surface — Dahua-native handlers ───────────────────

    def handle_behaviour_alert_simple(
        self,
        *,
        camera_id: int,
        alert_type: str,
        details: dict[str, Any],
    ) -> None:
        """Single-frame behaviour event from a camera-side rule.

        Used for line crossing, region intrusion, loitering, motion, work
        clothes — anything where the camera classified the event and we
        just need to log + open an incident.
        """
        state = self._touch_incident(camera_id)
        record = self._record("behaviour", {
            "alert_type": alert_type,
            "camera_id": camera_id,
            "details": details,
        }, incident_id=state.incident_id)
        self._publish(record, priority=Priority.INCIDENT_EVENT, endpoint="A4",
                      incident_id=state.incident_id)
        if state.stage == STAGE_PRELIMINARY:
            elapsed = state.last_event_at - state.first_event_at
            if elapsed >= state.dwell_required_s or alert_type == "loitering":
                self._promote(state, signal=f"dwell:{alert_type}")

    def handle_face_intruder(self, *, camera_id: int, face_bbox, name: str) -> None:
        state = self._touch_incident(camera_id)
        record = self._record("face_intruder", {
            "camera_id": camera_id,
            "bbox": [float(x) for x in face_bbox],
            "name": name,
        }, incident_id=state.incident_id)
        self._publish(record, priority=Priority.MEDIA_CAPTURE, endpoint="A6",
                      incident_id=state.incident_id)
        if state.stage == STAGE_PRELIMINARY:
            self._promote(state, signal="face_intruder")

    def handle_personnel_seen(
        self,
        *,
        camera_id: int,
        person_uid: str,
        group_id: str,
        name: str,
        similarity: int,
    ) -> None:
        """Camera matched a face to the workers group. Log only — no incident."""
        record = self._record("personnel_seen", {
            "camera_id": camera_id,
            "person_uid": person_uid,
            "group_id": group_id,
            "name": name,
            "similarity": similarity,
        })
        # Personnel sightings go to the intake at low priority so HQ can
        # build a presence log; they don't trigger A3/A8.
        self._publish(record, priority=Priority.HEARTBEAT, endpoint="A1",
                      incident_id=None)

    def handle_plate_capture(
        self,
        *,
        camera_id: int,
        plate_number: str,
        plate_color: Optional[str] = None,
        vehicle_color: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> None:
        record = self._record("plate_capture", {
            "camera_id": camera_id,
            "plate_number": plate_number,
            "plate_color": plate_color,
            "vehicle_color": vehicle_color,
            "speed": speed,
        })
        # The camera's onboard TrafficBlackList / TrafficRedList already
        # gates which plates trigger its own alarm rails. Here we always
        # forward the read so the backend can audit it.
        self._publish(record, priority=Priority.MEDIA_CAPTURE, endpoint="A6",
                      incident_id=None)

    def handle_fire(self, *, camera_id: int, details: dict[str, Any]) -> None:
        state = self._touch_incident(camera_id)
        record = self._record("fire", {"camera_id": camera_id, "details": details},
                              incident_id=state.incident_id)
        self._publish(record, priority=Priority.INCIDENT_EVENT, endpoint="A4",
                      incident_id=state.incident_id)
        # Life safety — promote immediately.
        if state.stage == STAGE_PRELIMINARY:
            self._promote(state, signal="fire")

    def handle_smoke(self, *, camera_id: int, details: dict[str, Any]) -> None:
        state = self._touch_incident(camera_id)
        record = self._record("smoke", {"camera_id": camera_id, "details": details},
                              incident_id=state.incident_id)
        self._publish(record, priority=Priority.INCIDENT_EVENT, endpoint="A4",
                      incident_id=state.incident_id)
        if state.stage == STAGE_PRELIMINARY:
            self._promote(state, signal="smoke")

    def handle_heartbeat(self, payload: dict[str, Any]) -> None:
        record = self._record("heartbeat", payload)
        self._publish(record, priority=Priority.HEARTBEAT, endpoint="A1", incident_id=None)

    # ─── inspection ───────────────────────────────────────────────

    def history(self) -> list[dict[str, Any]]:
        with self._history_lock:
            return list(self._history)

    def active_incidents(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "camera_id": s.camera_id,
                    "incident_id": s.incident_id,
                    "stage": s.stage,
                    "first_event_at": s.first_event_at,
                    "last_event_at": s.last_event_at,
                    "confirmation_signals": sorted(s.confirmation_signals),
                }
                for s in self._incidents.values()
            ]

    # ─── internals ────────────────────────────────────────────────

    def _touch_incident(self, camera_id: int) -> _IncidentState:
        now = time.monotonic()
        with self._lock:
            existing = self._incidents.get(camera_id)
            if existing is not None:
                existing.last_event_at = now
                return existing
        incident_id = self._packager.start_incident(
            site_id=self._tenant.site_id or "unknown",
            operator_id=self._tenant.operator_id or "unknown",
            device_id=self._tenant.device_id or "unknown",
        )
        state = _IncidentState(
            incident_id=incident_id,
            camera_id=camera_id,
            stage=STAGE_PRELIMINARY,
            first_event_at=now,
            last_event_at=now,
            dwell_required_s=self._dwell_s,
        )
        package_dir = self._packager_dir(incident_id)
        with self._lock:
            self._incidents[camera_id] = state
        self._intake.emit(
            priority=Priority.INCIDENT_EVENT,
            endpoint_hint="A3",
            payload={
                "site_id": self._tenant.site_id,
                "incident_id": incident_id,
                "incident_type": "preliminary_alert",
                "active_camera_ids": [f"cam{camera_id}"],
                "timestamp_utc": now_iso(),
            },
            incident_id=incident_id,
        )
        recorder = self._recorders.get(camera_id)
        if recorder is not None and package_dir is not None:
            try:
                recorder.begin(incident_id, package_dir)
            except Exception:
                logger.exception("cam%d: recorder.begin failed", camera_id)
        logger.info("cam%d: incident %s opened (preliminary)", camera_id, incident_id)
        return state

    def _promote(self, state: _IncidentState, *, signal: str) -> None:
        with self._lock:
            current = self._incidents.get(state.camera_id)
            if current is None or current.incident_id != state.incident_id:
                return
            if current.stage == STAGE_CONFIRMED:
                current.confirmation_signals.add(signal)
                return
            current.stage = STAGE_CONFIRMED
            current.confirmation_signals.add(signal)
        self._intake.emit(
            priority=Priority.INCIDENT_EVENT,
            endpoint_hint="A3",
            payload={
                "site_id": self._tenant.site_id,
                "incident_id": state.incident_id,
                "incident_type": "confirmed_incident",
                "confirmation_signal": signal,
                "active_camera_ids": [f"cam{state.camera_id}"],
                "timestamp_utc": now_iso(),
            },
            incident_id=state.incident_id,
        )
        logger.info("cam%d: incident %s CONFIRMED via %s",
                    state.camera_id, state.incident_id, signal)

    def _packager_dir(self, incident_id: str):
        store_root = getattr(self._packager, "store_root", None)
        if store_root is None:
            return None
        target = store_root / "incidents" / incident_id
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if target.exists():
                return target
            time.sleep(0.02)
        return target

    def _record(self, kind: str, body: dict[str, Any], *, incident_id: str | None = None) -> dict[str, Any]:
        return {
            "kind": kind,
            "site_id": self._tenant.site_id or None,
            "operator_id": self._tenant.operator_id or None,
            "device_id": self._tenant.device_id or None,
            "incident_id": incident_id,
            "timestamp_utc": now_iso(),
            "monotonic_ns": monotonic_ns(),
            **body,
        }

    def _publish(self, record: dict[str, Any], *, priority: Priority,
                 endpoint: str, incident_id: str | None) -> None:
        with self._history_lock:
            self._history.append(record)
        self._intake.emit(priority=priority, endpoint_hint=endpoint,
                          payload=record, incident_id=incident_id)
        if incident_id:
            self._packager.attach_event(incident_id, record)

    # ─── janitor ──────────────────────────────────────────────────

    def _janitor_loop(self) -> None:
        interval = max(0.1, min(2.0, self._idle_s / 3.0))
        while not self._janitor_stop.wait(timeout=interval):
            self._sweep_once()
        self._sweep_once(force=True)

    def _sweep_once(self, *, force: bool = False) -> None:
        now = time.monotonic()
        to_finalize: list[_IncidentState] = []
        with self._lock:
            for cam_id, state in list(self._incidents.items()):
                idle = now - state.last_event_at
                if force or idle >= self._idle_s:
                    to_finalize.append(state)
                    del self._incidents[cam_id]
        for state in to_finalize:
            self._finalize(state)

    def _finalize(self, state: _IncidentState) -> None:
        recorder = self._recorders.get(state.camera_id)
        if recorder is not None:
            try:
                recorder.finish()
            except Exception:
                logger.exception("cam%d: recorder.finish failed", state.camera_id)
        try:
            self._packager.finalize(state.incident_id, timeout=45.0)
        except Exception:
            logger.exception("cam%d: packager.finalize failed for %s",
                             state.camera_id, state.incident_id)
            return
        self._intake.emit(
            priority=Priority.EVIDENCE_PACKAGE,
            endpoint_hint="A5",
            payload={
                "site_id": self._tenant.site_id,
                "incident_id": state.incident_id,
                "stage": state.stage,
                "confirmation_signals": sorted(state.confirmation_signals),
                "timestamp_utc": now_iso(),
            },
            incident_id=state.incident_id,
        )
        logger.info("cam%d: incident %s finalised (%s)",
                    state.camera_id, state.incident_id, state.stage)
