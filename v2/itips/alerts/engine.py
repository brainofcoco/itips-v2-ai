"""Two-stage alert engine: PRELIMINARY → CONFIRMED.

In the Dahua-driven architecture the camera onboard AI does *every*
primary classification (face match, perimeter line cross, region entry,
loitering, plate read, fire, smoke). The Jetson's job here is just to
turn those classified events into incidents:

  * one PRELIMINARY incident per (camera, recent window),
  * promote to CONFIRMED on a verdict signal,
  * finalize idle incidents and ship the signed package via the intake.

Confirmation signals:
  - face_intruder: ThreatEvaluator's decision-window verdict, or a
                   FaceRecognition with empty Candidates from a camera
                   that bypasses the evaluator.
  - fire/smoke:    forced fast promotion (life safety).

Everything goes through the intake queue. We never call the cloud here.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from itips.sync.intake import IntakeWriter
from itips.sync.schema import Priority
from itips.utils.clock import monotonic_ns, now_iso

logger = logging.getLogger(__name__)

_MAX_HISTORY = 200

STAGE_PRELIMINARY = "preliminary"
STAGE_CONFIRMED = "confirmed"


@dataclass
class _PendingSensorCapture:
    """JPEG attached by a sensor itself (PIR-cam), waiting to be
    bound to an incident once one opens for the camera. Has a short
    TTL so we don't carry stale bytes if the trigger never escalates."""
    jpeg: bytes
    source: str
    zone_id: int
    zone_name: str
    ts_iso: str
    received_monotonic: float


@dataclass
class _IncidentState:
    incident_id: str
    camera_id: int
    stage: str
    first_event_at: float
    last_event_at: float
    package_dir: Optional[object] = None
    confirmation_signals: set[str] = field(default_factory=set)


class AlertEngine:
    def __init__(
        self,
        *,
        intake: IntakeWriter,
        evidence_packager,
        tenant,
        recorders: Optional[dict[int, object]] = None,
        idle_timeout_seconds: float = 15.0,
    ) -> None:
        self._intake = intake
        self._packager = evidence_packager
        self._tenant = tenant
        self._recorders: dict[int, object] = recorders or {}
        self._idle_s = idle_timeout_seconds
        self._history: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
        self._history_lock = threading.Lock()
        self._incidents: dict[int, _IncidentState] = {}
        self._lock = threading.Lock()
        self._janitor_stop = threading.Event()
        self._janitor: Optional[threading.Thread] = None
        # Listener hooks — out-of-band consumers (webhook dispatcher,
        # local automation) subscribe here. Callbacks run synchronously
        # on the producer's thread, so they must not block.
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lifecycle_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        # Per-camera buffer of sensor-attached JPEGs (currently AX PRO
        # pircam) waiting for an incident to attach themselves to.
        # The PIR fires several seconds before the dispatch chain
        # produces a confirmed alert, so we hold the bytes briefly and
        # drain into the incident when _touch_incident creates one.
        self._pending_sensor_captures: dict[int, list[_PendingSensorCapture]] = {}
        self._pending_sensor_lock = threading.Lock()
        self._pending_sensor_ttl_s = 60.0

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

    def add_event_listener(
        self, listener: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to every record this engine publishes.

        The listener receives the same dict appended to history — so
        downstream consumers (webhooks, on-prem automation) see exactly
        what the SSE feed shows. Must not block.
        """
        self._event_listeners.append(listener)

    def add_lifecycle_listener(
        self, listener: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Subscribe to incident open / promote / finalize transitions.

        Stage strings: 'preliminary', 'confirmed', 'finalized'. The info
        dict carries incident_id, camera_id, signal (where applicable),
        and tenant identifiers.
        """
        self._lifecycle_listeners.append(listener)

    def feed_frame(self, camera_id: int, frame) -> None:
        rec = self._recorders.get(camera_id)
        if rec is not None:
            try:
                rec.feed(frame)
            except Exception:
                logger.exception("cam%d: recorder.feed failed", camera_id)

    def record_sensor_capture(
        self,
        camera_id: int,
        *,
        jpeg: bytes,
        source: str,
        zone_id: int,
        zone_name: str = "",
    ) -> None:
        """Stash a sensor-attached JPEG (e.g. PIR-cam alarm picture).

        If an incident is already open for `camera_id`, the capture is
        attached directly. Otherwise we buffer it for a short window
        and drain on the next incident open for that camera — the
        dispatch chain typically takes 3–6 s to confirm an event, but
        the PIR-cam picture arrives almost immediately, so without
        this buffer the picture would beat the incident and be lost.
        """
        if not jpeg:
            return
        now_mono = time.monotonic()
        entry = _PendingSensorCapture(
            jpeg=jpeg, source=source, zone_id=int(zone_id),
            zone_name=zone_name, ts_iso=now_iso(),
            received_monotonic=now_mono,
        )
        # If there's already an active incident on this camera, attach now.
        with self._lock:
            active = self._incidents.get(camera_id)
        if active is not None:
            self._packager.attach_sensor_capture(
                active.incident_id, jpeg=jpeg, source=source,
                zone_id=int(zone_id), zone_name=zone_name, ts=entry.ts_iso,
            )
            logger.info(
                "cam%d: %s capture (zone=%d, %dB JPEG) attached to active "
                "incident %s",
                camera_id, source, zone_id, len(jpeg), active.incident_id,
            )
            return
        # No incident yet — buffer briefly. GC stale entries to keep
        # the buffer from leaking memory under a chatty PIR-cam.
        with self._pending_sensor_lock:
            queue = self._pending_sensor_captures.setdefault(camera_id, [])
            queue.append(entry)
            cutoff = now_mono - self._pending_sensor_ttl_s
            self._pending_sensor_captures[camera_id] = [
                e for e in queue if e.received_monotonic >= cutoff
            ]
        logger.info(
            "cam%d: %s capture (zone=%d, %dB JPEG) buffered — "
            "waiting for incident to open",
            camera_id, source, zone_id, len(jpeg),
        )

    def _drain_sensor_buffer(self, camera_id: int, incident_id: str) -> None:
        with self._pending_sensor_lock:
            queue = self._pending_sensor_captures.pop(camera_id, [])
        if not queue:
            return
        now_mono = time.monotonic()
        cutoff = now_mono - self._pending_sensor_ttl_s
        fresh = [e for e in queue if e.received_monotonic >= cutoff]
        for entry in fresh:
            try:
                self._packager.attach_sensor_capture(
                    incident_id,
                    jpeg=entry.jpeg, source=entry.source,
                    zone_id=entry.zone_id, zone_name=entry.zone_name,
                    ts=entry.ts_iso,
                )
            except Exception:
                logger.exception(
                    "cam%d: failed to attach buffered %s capture",
                    camera_id, entry.source,
                )
        if fresh:
            logger.info(
                "cam%d: drained %d buffered sensor capture(s) into incident %s",
                camera_id, len(fresh), incident_id,
            )

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

        Logs the event and opens (or updates) a preliminary incident.
        Promotion to CONFIRMED comes from explicit verdict signals
        (face_intruder, fire, smoke) — not from dwell accumulation, which
        used to silently confirm any preliminary that saw two events.
        """
        state = self._touch_incident(camera_id)
        record = self._record("behaviour", {
            "alert_type": alert_type,
            "camera_id": camera_id,
            "details": details,
        }, incident_id=state.incident_id)
        self._publish(record, priority=Priority.INCIDENT_EVENT, endpoint="A4",
                      incident_id=state.incident_id)

    def handle_face_intruder(
        self,
        *,
        camera_id: int,
        face_bbox,
        name: str,
        details: dict[str, Any] | None = None,
        jpeg: Optional[bytes] = None,
    ) -> None:
        state = self._touch_incident(camera_id)
        record = self._record("face_intruder", {
            "camera_id": camera_id,
            "bbox": [float(x) for x in face_bbox],
            "name": name,
            **(details or {}),
        }, incident_id=state.incident_id)
        self._publish(record, priority=Priority.MEDIA_CAPTURE, endpoint="A6",
                      incident_id=state.incident_id)
        if jpeg:
            self._packager.attach_face_capture(
                state.incident_id, jpeg=jpeg,
                confidence=0.0, name=name,
            )
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
        details: dict[str, Any] | None = None,
        jpeg: Optional[bytes] = None,
    ) -> None:
        """Camera matched a face to the workers group. Log only — no incident."""
        record = self._record("personnel_seen", {
            "camera_id": camera_id,
            "person_uid": person_uid,
            "group_id": group_id,
            "name": name,
            "similarity": similarity,
            **(details or {}),
        })
        # personnel_seen is presence-tracking, not an incident, so face
        # JPEGs only land in the package if an incident is already open
        # for this camera (e.g. concurrent intruder alert).
        if jpeg:
            with self._lock:
                state = self._incidents.get(camera_id)
            if state is not None:
                self._packager.attach_face_capture(
                    state.incident_id, jpeg=jpeg,
                    confidence=float(similarity) / 100.0, name=name,
                )
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
        jpeg: Optional[bytes] = None,
        confidence: float = 0.0,
    ) -> None:
        record = self._record("plate_capture", {
            "camera_id": camera_id,
            "plate_number": plate_number,
            "plate_color": plate_color,
            "vehicle_color": vehicle_color,
            "speed": speed,
            "confidence": confidence,
        })
        # TrafficBlackList / TrafficRedList on the camera gate alarms;
        # we always forward the read so the backend can audit.
        self._publish(record, priority=Priority.MEDIA_CAPTURE, endpoint="A6",
                      incident_id=None)
        # Persist the plate crop into the package when an incident is open.
        if jpeg:
            with self._lock:
                state = self._incidents.get(camera_id)
            if state is not None:
                self._packager.attach_plate_capture(
                    state.incident_id, jpeg=jpeg,
                    plate_number=plate_number, confidence=confidence,
                )

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
        # Record the opening transition so finalize() can write
        # alert_stage_log per PRD §4.3 REQ-EV-01.
        self._packager.attach_event(incident_id, {
            "kind": "stage_change",
            "stage": STAGE_PRELIMINARY,
            "signal": "first_event",
            "camera_id": camera_id,
            "timestamp_utc": now_iso(),
        })
        logger.info("cam%d: incident %s opened (preliminary)", camera_id, incident_id)
        # Drain any sensor-attached JPEGs (PIR-cam alarm pictures) that
        # arrived before the dispatch chain confirmed the event.
        self._drain_sensor_buffer(camera_id, incident_id)
        self._notify_lifecycle("preliminary", {
            "incident_id": incident_id,
            "camera_id": camera_id,
            "site_id": self._tenant.site_id or None,
            "operator_id": self._tenant.operator_id or None,
            "device_id": self._tenant.device_id or None,
            "stage": STAGE_PRELIMINARY,
            "timestamp_utc": now_iso(),
        })
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
        self._packager.attach_event(state.incident_id, {
            "kind": "stage_change",
            "stage": STAGE_CONFIRMED,
            "signal": signal,
            "camera_id": state.camera_id,
            "timestamp_utc": now_iso(),
        })
        logger.info("cam%d: incident %s CONFIRMED via %s",
                    state.camera_id, state.incident_id, signal)
        self._notify_lifecycle("confirmed", {
            "incident_id": state.incident_id,
            "camera_id": state.camera_id,
            "site_id": self._tenant.site_id or None,
            "operator_id": self._tenant.operator_id or None,
            "device_id": self._tenant.device_id or None,
            "stage": STAGE_CONFIRMED,
            "signal": signal,
            "timestamp_utc": now_iso(),
        })

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
        self._notify_event(record)

    def _notify_event(self, record: dict[str, Any]) -> None:
        for listener in self._event_listeners:
            try:
                listener(record)
            except Exception:
                logger.exception("alert event listener failed (kind=%s)",
                                 record.get("kind"))

    def _notify_lifecycle(self, stage: str, info: dict[str, Any]) -> None:
        for listener in self._lifecycle_listeners:
            try:
                listener(stage, info)
            except Exception:
                logger.exception("alert lifecycle listener failed (stage=%s)", stage)

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
        self._notify_lifecycle("finalized", {
            "incident_id": state.incident_id,
            "camera_id": state.camera_id,
            "site_id": self._tenant.site_id or None,
            "operator_id": self._tenant.operator_id or None,
            "device_id": self._tenant.device_id or None,
            "stage": state.stage,
            "confirmation_signals": sorted(state.confirmation_signals),
            "timestamp_utc": now_iso(),
        })
