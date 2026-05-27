"""Single-funnel threat evaluator with a multi-frame decision window.

Why this exists
---------------
The legacy flow was "every camera/sensor event opens an incident". That
fires false alarms whenever the camera saw a worker but FR couldn't match
them in the one frame attached to the event (back-to-camera, motion blur,
bad lighting). Face recognition is the gate that decides "registered
worker" vs "intruder", so a single-frame verdict is too brittle.

The funnel
----------
Every primary trigger — camera line-cross, region intrusion, face event,
AX PRO sensor activation — collapses into one call:

    evaluator.trigger(camera_id=N, trigger_kind="...", initial_frame=...)

The evaluator opens (or extends) a per-camera **decision window**
(default 15s). A background worker samples snapshots from the camera
every ~1s during the window and runs the face engine on each. Three
verdicts close the window:

  * `AUTHORIZED` — at least one frame matched an enrolled person at the
    engine's similarity threshold. Closes immediately, no alarm; emits
    `personnel_seen` for the presence log.
  * `INTRUDER`   — at least one frame contained a face that was
    confidently not a match, and no AUTHORIZED frame was ever seen.
    Fires `handle_face_intruder` (and the existing webhook + auto-siren
    plumbing kicks in via the confirmed-incident lifecycle). Suppressed
    when the AX PRO is disarmed.
  * `UNCERTAIN`  — the window expired without any usable face. The
    classic back-to-camera worker case. Log-only; no alarm.

Subsequent triggers during an open window are appended (not stacked
into new windows) and extend the deadline so a flurry of motion +
line-cross + face events all roll into the same verdict.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class ThreatVerdict(str, enum.Enum):
    AUTHORIZED = "authorized"
    INTRUDER = "intruder"
    UNCERTAIN = "uncertain"


@dataclass
class _Window:
    camera_id: int
    opened_at: float
    expires_at: float
    triggers: list[str] = field(default_factory=list)
    trigger_details: list[dict[str, Any]] = field(default_factory=list)
    initial_frame: Optional["np.ndarray"] = None
    samples_taken: int = 0
    saw_face: bool = False
    best_no_match_sim: float = 0.0
    best_match_result: Optional[Any] = None  # RecognitionResult — opaque here
    best_match_frame: Optional["np.ndarray"] = None
    closed: bool = False


class ThreatEvaluator:
    """Multi-frame face-gated verdict engine. One worker thread, many windows."""

    def __init__(
        self,
        *,
        alert_engine,
        dahua_manager,
        face_engine,
        is_armed_fn: Optional[Callable[[], bool]] = None,
        window_seconds: float = 15.0,
        sample_interval_s: float = 1.0,
        event_tap=None,
    ) -> None:
        self._alert_engine = alert_engine
        self._dahua_manager = dahua_manager
        self._face_engine = face_engine
        # Default to "armed" when no hub is wired — otherwise we'd silently
        # swallow every intruder verdict on dev machines.
        self._is_armed_fn = is_armed_fn or (lambda: True)
        self._window_s = float(window_seconds)
        self._sample_interval = max(0.2, float(sample_interval_s))
        self._event_tap = event_tap

        self._windows: dict[int, _Window] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tick = threading.Event()
        self._worker: Optional[threading.Thread] = None
        # Out-of-band consumers (webhook dispatcher, hub control). Each
        # listener receives the same payload dict the dispatch helpers
        # below build. Runs on the worker thread → must not block.
        self._verdict_listeners: list[Callable[[dict[str, Any]], None]] = []

    # ─── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, name="threat-evaluator", daemon=True,
        )
        self._worker.start()
        logger.info(
            "ThreatEvaluator started — window=%.1fs sample=%.1fs",
            self._window_s, self._sample_interval,
        )

    def stop(self) -> None:
        self._stop.set()
        self._tick.set()
        if self._worker is not None:
            self._worker.join(timeout=5.0)
        self._worker = None

    def add_verdict_listener(
        self, fn: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to every closed-window verdict.

        Used by the webhook dispatcher to publish `incident.verdict` and
        by hub-control code that needs the verdict alongside the alarm.
        Listeners run on the worker thread, so they must return quickly.
        """
        self._verdict_listeners.append(fn)

    def set_is_armed_fn(self, fn: Callable[[], bool]) -> None:
        """Rebind the disarm gate post-construction.

        Needed because the AX PRO listener is built after the evaluator
        (the sensor dispatcher depends on the evaluator, the listener
        depends on the dispatcher). app.py calls this once the listener
        exists so disarmed mode suppresses INTRUDER verdicts.
        """
        self._is_armed_fn = fn

    # ─── public surface ───────────────────────────────────────────────

    def trigger(
        self,
        *,
        camera_id: int,
        trigger_kind: str,
        initial_frame: Optional["np.ndarray"] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Open or extend the window for this camera. Idempotent — a
        flurry of triggers rolls into one verdict instead of N incidents."""
        if camera_id <= 0:
            logger.debug("threat eval: ignoring trigger with no camera (%s)",
                         trigger_kind)
            return
        now = time.monotonic()
        new_window = False
        with self._lock:
            w = self._windows.get(camera_id)
            if w is None or w.closed:
                w = _Window(
                    camera_id=camera_id,
                    opened_at=now,
                    expires_at=now + self._window_s,
                    initial_frame=initial_frame,
                )
                self._windows[camera_id] = w
                new_window = True
            else:
                # Extend deadline — a moving subject should keep us watching.
                w.expires_at = max(w.expires_at, now + self._window_s)
                # Use the latest event frame as a sample if we haven't
                # consumed one yet.
                if initial_frame is not None and w.initial_frame is None:
                    w.initial_frame = initial_frame
            w.triggers.append(trigger_kind)
            if details:
                w.trigger_details.append({"kind": trigger_kind, **details})
        if new_window:
            logger.info(
                "threat eval cam %d: window OPEN by %s",
                camera_id, trigger_kind,
            )
        else:
            logger.debug(
                "threat eval cam %d: window EXTENDED by %s",
                camera_id, trigger_kind,
            )
        # Wake the worker so it samples immediately on the first trigger.
        self._tick.set()

    def active_windows(self) -> list[dict[str, Any]]:
        """Snapshot of in-flight windows — used by the dashboard."""
        with self._lock:
            return [
                {
                    "camera_id": w.camera_id,
                    "opened_at": w.opened_at,
                    "expires_at": w.expires_at,
                    "triggers": list(w.triggers),
                    "samples_taken": w.samples_taken,
                    "saw_face": w.saw_face,
                    "best_no_match_sim": round(w.best_no_match_sim, 3),
                    "authorized": bool(w.best_match_result and
                                       getattr(w.best_match_result, "matched", False)),
                }
                for w in self._windows.values()
                if not w.closed
            ]

    # ─── worker loop ──────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            self._sample_open_windows(now)
            self._finalize_expired(time.monotonic())
            self._tick.wait(timeout=self._sample_interval)
            self._tick.clear()

    def _sample_open_windows(self, now: float) -> None:
        with self._lock:
            todo = [(cam_id, w) for cam_id, w in self._windows.items()
                    if not w.closed]
        for cam_id, w in todo:
            # Skip cameras that already produced an AUTHORIZED hit; the
            # finalize sweep will remove them.
            if w.best_match_result is not None and getattr(
                w.best_match_result, "matched", False
            ):
                continue
            frame = self._next_sample_frame(w, cam_id)
            with self._lock:
                w.samples_taken += 1
            if frame is None:
                continue
            self._evaluate_frame(w, frame)

    def _next_sample_frame(
        self, w: _Window, cam_id: int,
    ) -> Optional["np.ndarray"]:
        """Use the trigger's attached frame the first time around; pull a
        fresh snapshot every subsequent tick so we get multiple angles."""
        with self._lock:
            if w.initial_frame is not None:
                frame = w.initial_frame
                w.initial_frame = None
                return frame
        if self._dahua_manager is None:
            return None
        client = self._dahua_manager.get(cam_id)
        if client is None:
            return None
        try:
            return client.endpoint.snapshot(timeout=3.0)
        except Exception:
            logger.exception("threat eval cam %d: snapshot failed", cam_id)
            return None

    def _evaluate_frame(self, w: _Window, frame: "np.ndarray") -> None:
        if self._face_engine is None:
            return
        try:
            result = self._face_engine.recognize(frame)
        except Exception:
            logger.exception(
                "threat eval cam %d: face engine crashed", w.camera_id,
            )
            return
        # `embedding is None` ⇒ no face detected in this frame. That's the
        # back-to-camera case — leave the window open, hoping a later
        # sample catches a face.
        if getattr(result, "embedding", None) is None:
            return
        with self._lock:
            w.saw_face = True
            if getattr(result, "matched", False):
                # Close on the first authorized match — no need to keep
                # sampling once the worker is confirmed safe.
                w.best_match_result = result
                w.best_match_frame = frame
                w.closed = True
                authorized_close = True
            else:
                authorized_close = False
                sim = float(getattr(result, "similarity", 0.0) or 0.0)
                if sim > w.best_no_match_sim:
                    w.best_no_match_sim = sim
        if authorized_close:
            self._dispatch_authorized(w, result, frame)

    def _finalize_expired(self, now: float) -> None:
        to_finalize: list[_Window] = []
        with self._lock:
            for cam_id in list(self._windows):
                w = self._windows[cam_id]
                if w.closed:
                    self._windows.pop(cam_id, None)
                    continue
                if now >= w.expires_at:
                    w.closed = True
                    to_finalize.append(w)
                    self._windows.pop(cam_id, None)
        for w in to_finalize:
            self._finalize(w)

    def _finalize(self, w: _Window) -> None:
        # AUTHORIZED was dispatched the moment it happened; nothing left.
        if w.best_match_result is not None and getattr(
            w.best_match_result, "matched", False
        ):
            return
        if not w.saw_face:
            self._dispatch_uncertain(w)
            return
        self._dispatch_intruder(w)

    # ─── verdict dispatch ─────────────────────────────────────────────

    def _dispatch_authorized(
        self, w: _Window, result, frame: "np.ndarray",
    ) -> None:
        sim_pct = int(round(float(getattr(result, "similarity", 0.0)) * 100))
        person_uid = str(getattr(result, "person_id", "") or "")
        person_name = str(getattr(result, "full_name", "") or "")
        try:
            self._alert_engine.handle_personnel_seen(
                camera_id=w.camera_id,
                person_uid=person_uid,
                group_id="threat-evaluator",
                name=person_name,
                similarity=sim_pct,
                jpeg=_encode_jpeg(frame),
            )
        except Exception:
            logger.exception("threat eval cam %d: personnel_seen dispatch failed",
                             w.camera_id)
        logger.info(
            "threat eval cam %d: AUTHORIZED — %s sim=%d%% triggers=%s samples=%d",
            w.camera_id, person_name or "?",
            sim_pct, _trigger_summary(w), w.samples_taken,
        )
        self._notify_verdict(self._verdict_payload(
            w, verdict=ThreatVerdict.AUTHORIZED, armed=True,
            extra={
                "person_uid": person_uid,
                "person_name": person_name,
                "similarity": round(float(getattr(result, "similarity", 0.0)), 3),
            },
        ))

    def _dispatch_intruder(self, w: _Window) -> None:
        details = {
            "triggers": list(w.triggers),
            "samples": w.samples_taken,
            "best_no_match_sim": round(w.best_no_match_sim, 3),
            "window_s": round(time.monotonic() - w.opened_at, 1),
            "verdict": ThreatVerdict.INTRUDER.value,
        }
        armed = True
        try:
            armed = bool(self._is_armed_fn())
        except Exception:
            logger.exception(
                "threat eval cam %d: is_armed callback crashed; assuming armed",
                w.camera_id,
            )
        if not armed:
            logger.info(
                "threat eval cam %d: INTRUDER suppressed (system disarmed) "
                "triggers=%s samples=%d best_no_match_sim=%.2f",
                w.camera_id, _trigger_summary(w),
                w.samples_taken, w.best_no_match_sim,
            )
            # Still record the observation so the audit trail shows what
            # the camera saw while the panel was disarmed.
            try:
                self._alert_engine.handle_behaviour_alert_simple(
                    camera_id=w.camera_id,
                    alert_type="threat_observation_disarmed",
                    details=details,
                )
            except Exception:
                logger.exception(
                    "threat eval cam %d: disarmed-observation log failed",
                    w.camera_id,
                )
            self._notify_verdict(self._verdict_payload(
                w, verdict=ThreatVerdict.INTRUDER, armed=False,
            ))
            return
        try:
            self._alert_engine.handle_face_intruder(
                camera_id=w.camera_id,
                face_bbox=(0.0, 0.0, 0.0, 0.0),
                name="INTRUDER",
                details=details,
            )
        except Exception:
            logger.exception("threat eval cam %d: face_intruder dispatch failed",
                             w.camera_id)
        logger.warning(
            "threat eval cam %d: INTRUDER — triggers=%s samples=%d "
            "best_no_match_sim=%.2f",
            w.camera_id, _trigger_summary(w),
            w.samples_taken, w.best_no_match_sim,
        )
        self._notify_verdict(self._verdict_payload(
            w, verdict=ThreatVerdict.INTRUDER, armed=True,
        ))

    def _dispatch_uncertain(self, w: _Window) -> None:
        details = {
            "triggers": list(w.triggers),
            "samples": w.samples_taken,
            "window_s": round(time.monotonic() - w.opened_at, 1),
            "verdict": ThreatVerdict.UNCERTAIN.value,
        }
        try:
            self._alert_engine.handle_behaviour_alert_simple(
                camera_id=w.camera_id,
                alert_type="threat_uncertain",
                details=details,
            )
        except Exception:
            logger.exception(
                "threat eval cam %d: uncertain-observation log failed",
                w.camera_id,
            )
        logger.info(
            "threat eval cam %d: UNCERTAIN — no face visible, triggers=%s "
            "samples=%d",
            w.camera_id, _trigger_summary(w), w.samples_taken,
        )
        self._notify_verdict(self._verdict_payload(
            w, verdict=ThreatVerdict.UNCERTAIN, armed=True,
        ))


    # ─── verdict listener fan-out ─────────────────────────────────────

    def _verdict_payload(
        self,
        w: _Window,
        *,
        verdict: ThreatVerdict,
        armed: bool,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build the wire-shape passed to verdict listeners.

        Same shape across all three verdicts so subscribers can switch on
        `verdict` without branching by field presence."""
        payload: dict[str, Any] = {
            "verdict": verdict.value,
            "camera_id": w.camera_id,
            "armed": bool(armed),
            "triggers": list(w.triggers),
            "trigger_details": list(w.trigger_details),
            "samples": w.samples_taken,
            "saw_face": w.saw_face,
            "best_no_match_sim": round(w.best_no_match_sim, 3),
            "window_s": round(time.monotonic() - w.opened_at, 2),
            "alarm_fired": (verdict is ThreatVerdict.INTRUDER and armed),
        }
        if extra:
            payload.update(extra)
        return payload

    def _notify_verdict(self, payload: dict[str, Any]) -> None:
        for listener in self._verdict_listeners:
            try:
                listener(payload)
            except Exception:
                logger.exception(
                    "threat eval: verdict listener failed (cam %s verdict=%s)",
                    payload.get("camera_id"), payload.get("verdict"),
                )


# ─── helpers ─────────────────────────────────────────────────────────


def _trigger_summary(w: _Window) -> str:
    return ",".join(w.triggers[:6]) + ("..." if len(w.triggers) > 6 else "")


def _encode_jpeg(frame) -> Optional[bytes]:
    if frame is None:
        return None
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else None
    except Exception:
        return None
