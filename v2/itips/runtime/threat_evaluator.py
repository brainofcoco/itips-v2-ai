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
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
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
    # Monotonic time of the most recent trigger and the most recent frame a
    # face was actually seen in. Together they say "the subject is still
    # here" — the gate for dwell-based early escalation.
    last_trigger_at: float = 0.0
    last_face_at: float = 0.0
    best_no_match_sim: float = 0.0
    best_match_result: Optional[Any] = None  # RecognitionResult — opaque here
    best_match_frame: Optional["np.ndarray"] = None
    # Recent sampled frames as encoded JPEG bytes (kept small, not the raw
    # 4K numpy arrays) so the Investigations page can show what the camera
    # saw during the evaluation — even when no incident was created.
    sample_jpegs: deque = field(default_factory=lambda: deque(maxlen=4))
    # Annotated detection frames pushed in by the BehaviorWatcher (zone
    # outline + detection box drawn). These are the strongest evidence —
    # they actually contain the subject in the zone — so they lead the
    # capture set ahead of the evaluator's own (often empty) snapshots.
    evidence_jpegs: deque = field(default_factory=lambda: deque(maxlen=3))
    closed: bool = False


@dataclass
class _Holdoff:
    """Per-camera suppression after an AUTHORIZED verdict.

    While a hold-off is active the camera's triggers are ignored. The
    worker loop polls the camera for face presence; once no face has been
    seen for `holdoff_clear_seconds` the hold-off lifts and normal
    evaluation resumes.
    """
    entered_at: float
    person_name: str = ""
    person_uid: str = ""
    # Monotonic time the frame first went clear of faces; None while a
    # face is still visible. Re-armed when (now - clear_since) ≥ threshold.
    clear_since: Optional[float] = None
    samples_taken: int = 0


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
        holdoff_clear_seconds: float = 15.0,
        escalate_after_seconds: float = 3.0,
        clip_pre_seconds: float = 15.0,
        clip_post_seconds: float = 15.0,
        recorders: Optional[dict[int, Any]] = None,
        event_tap=None,
        capture_dir: Optional[Path] = None,
    ) -> None:
        self._alert_engine = alert_engine
        self._dahua_manager = dahua_manager
        self._face_engine = face_engine
        # Where to persist a few representative JPEGs per verdict so the
        # Investigations page can show the evidence. None disables capture.
        self._capture_dir = Path(capture_dir) if capture_dir is not None else None
        # Default to "armed" when no hub is wired — otherwise we'd silently
        # swallow every intruder verdict on dev machines.
        self._is_armed_fn = is_armed_fn or (lambda: True)
        self._window_s = float(window_seconds)
        self._sample_interval = max(0.2, float(sample_interval_s))
        self._holdoff_clear_s = float(holdoff_clear_seconds)
        # Dwell escalation: once a confirmed stranger (a seen-but-unmatched
        # face) has been continuously present this long, fire INTRUDER early
        # instead of waiting out the full window.
        self._escalate_after_s = float(escalate_after_seconds)
        # "Still present" gate: a trigger or a seen face within this many
        # seconds means the subject is actively dwelling (not a stale window
        # lingering after they left). Tied to the trigger/sample cadence.
        self._presence_grace_s = max(2.0, self._sample_interval * 2.0)
        # Per-camera IncidentRecorders, used to cut a verdict evidence clip
        # from the pre-event ring buffer on UNCERTAIN.
        self._recorders = recorders or {}
        self._clip_pre_s = float(clip_pre_seconds)
        self._clip_post_s = float(clip_post_seconds)
        self._event_tap = event_tap

        self._windows: dict[int, _Window] = {}
        # Cameras suppressed after an AUTHORIZED verdict, keyed by camera_id.
        self._holdoff: dict[int, _Holdoff] = {}
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
        evidence_jpeg: Optional[bytes] = None,
    ) -> None:
        """Open or extend the window for this camera. Idempotent — a
        flurry of triggers rolls into one verdict instead of N incidents.

        `evidence_jpeg` is an already-encoded annotated frame (zone +
        detection box) the caller wants surfaced on Investigations."""
        if camera_id <= 0:
            logger.debug("threat eval: ignoring trigger with no camera (%s)",
                         trigger_kind)
            return
        now = time.monotonic()
        # Hold-off gate: after an AUTHORIZED worker we stop evaluating this
        # camera until its frame clears. A face event means the worker is
        # still in view, so reset the clear countdown; everything else is
        # dropped. The worker loop owns lifting the hold-off.
        with self._lock:
            holdoff = self._holdoff.get(camera_id)
            if holdoff is not None:
                if "face" in trigger_kind:
                    holdoff.clear_since = None
                logger.debug(
                    "threat eval cam %d: trigger %s ignored (hold-off)",
                    camera_id, trigger_kind,
                )
                return
        new_window = False
        with self._lock:
            w = self._windows.get(camera_id)
            if w is None or w.closed:
                w = _Window(
                    camera_id=camera_id,
                    opened_at=now,
                    expires_at=now + self._window_s,
                    initial_frame=initial_frame,
                    last_trigger_at=now,
                )
                self._windows[camera_id] = w
                new_window = True
            else:
                # Extend deadline — a moving subject should keep us watching.
                w.expires_at = max(w.expires_at, now + self._window_s)
                w.last_trigger_at = now
                # Use the latest event frame as a sample if we haven't
                # consumed one yet.
                if initial_frame is not None and w.initial_frame is None:
                    w.initial_frame = initial_frame
            w.triggers.append(trigger_kind)
            if details:
                w.trigger_details.append({"kind": trigger_kind, **details})
            if evidence_jpeg:
                w.evidence_jpegs.append(evidence_jpeg)
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

    def holdoff_person(self, camera_id: int) -> Optional[str]:
        """Name of the authorized worker a camera is currently held off for,
        or None when the camera isn't in hold-off. The Live feed uses this to
        badge activity as 'authorized' (instead of an intrusion) while a
        recognised worker is still in view."""
        with self._lock:
            h = self._holdoff.get(camera_id)
            return h.person_name if h is not None else None

    # ─── worker loop ──────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            self._sample_open_windows(now)
            self._check_escalations(time.monotonic())
            self._finalize_expired(time.monotonic())
            self._poll_holdoffs(time.monotonic())
            self._tick.wait(timeout=self._sample_interval)
            self._tick.clear()

    def _check_escalations(self, now: float) -> None:
        """Fire INTRUDER early when an unrecognised person keeps dwelling.

        Conditions (person-presence): the window is open and no enrolled
        worker has matched, the subject is still present (a trigger or a seen
        face within the presence grace — sustained triggers mean YOLO/the
        camera still sees them in the zone), and they've been around at least
        `_escalate_after_s`. No face is required: someone who walks in and
        lingers without ever being recognised is escalated regardless of
        whether their face was captured. Authorized workers are still safe —
        a match closes the window AUTHORIZED (and starts hold-off) before
        this fires; a brief, non-dwelling trigger fades out of the presence
        grace and never escalates."""
        if self._escalate_after_s <= 0:
            return
        to_escalate: list[_Window] = []
        with self._lock:
            for cam_id, w in list(self._windows.items()):
                if w.closed:
                    continue
                if w.best_match_result is not None and getattr(
                    w.best_match_result, "matched", False
                ):
                    continue
                if (now - w.opened_at) < self._escalate_after_s:
                    continue
                last_seen = max(w.last_trigger_at, w.last_face_at)
                if (now - last_seen) > self._presence_grace_s:
                    continue
                w.closed = True
                self._windows.pop(cam_id, None)
                to_escalate.append(w)
        for w in to_escalate:
            self._dispatch_intruder(w, escalated_early=True)

    def _poll_holdoffs(self, now: float) -> None:
        """Watch each held-off camera for face presence; lift the hold-off
        once the frame has been clear of faces for `_holdoff_clear_s`."""
        with self._lock:
            cam_ids = list(self._holdoff)
        for cam_id in cam_ids:
            frame = self._snapshot(cam_id)
            if frame is None:
                continue
            face_present = self._frame_has_face(frame)
            rearm = False
            with self._lock:
                h = self._holdoff.get(cam_id)
                if h is None:
                    continue
                h.samples_taken += 1
                if face_present:
                    h.clear_since = None
                elif h.clear_since is None:
                    h.clear_since = now
                elif now - h.clear_since >= self._holdoff_clear_s:
                    self._holdoff.pop(cam_id, None)
                    rearm = True
            if rearm:
                logger.info(
                    "threat eval cam %d: hold-off OFF — frame clear ≥%.0fs, "
                    "resuming evaluation", cam_id, self._holdoff_clear_s,
                )

    def _frame_has_face(self, frame: "np.ndarray") -> bool:
        """True when the face engine detects a face in `frame`. Identity is
        irrelevant here — any face means the view isn't clear yet."""
        if self._face_engine is None:
            return False
        try:
            result = self._face_engine.recognize(frame)
        except Exception:
            # On engine error, treat as 'face present' so a transient
            # failure can't prematurely lift the hold-off.
            logger.exception("threat eval: hold-off face check crashed")
            return True
        return getattr(result, "embedding", None) is not None

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
            if self._capture_dir is not None:
                jpeg = _encode_capture_jpeg(frame)
                if jpeg:
                    with self._lock:
                        w.sample_jpegs.append(jpeg)
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
        return self._snapshot(cam_id)

    def _snapshot(self, cam_id: int) -> Optional["np.ndarray"]:
        """Pull a fresh snapshot from the camera. None on any failure."""
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
            w.last_face_at = time.monotonic()
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
        # Hold off this camera so the recognised worker isn't re-evaluated
        # on every subsequent event while they're still in view.
        with self._lock:
            self._holdoff[w.camera_id] = _Holdoff(
                entered_at=time.monotonic(),
                person_name=person_name,
                person_uid=person_uid,
            )
        logger.info(
            "threat eval cam %d: hold-off ON after AUTHORIZED (%s)",
            w.camera_id, person_name or "?",
        )
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

    def _dispatch_intruder(self, w: _Window, *, escalated_early: bool = False) -> None:
        dwell_s = round(time.monotonic() - w.opened_at, 1)
        details = {
            "triggers": list(w.triggers),
            "samples": w.samples_taken,
            "best_no_match_sim": round(w.best_no_match_sim, 3),
            "window_s": dwell_s,
            "verdict": ThreatVerdict.INTRUDER.value,
            "escalated_early": escalated_early,
            "dwell_s": dwell_s,
        }
        extra = {"escalated_early": escalated_early}
        how = (f"early (dwell ≥{self._escalate_after_s:.0f}s)"
               if escalated_early else "window expired")
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
                "threat eval cam %d: INTRUDER suppressed (system disarmed, %s) "
                "triggers=%s samples=%d best_no_match_sim=%.2f",
                w.camera_id, how, _trigger_summary(w),
                w.samples_taken, w.best_no_match_sim,
            )
            # Record the observation as a verdict only (→ Investigations).
            # We deliberately do NOT open an incident here: a disarmed
            # panel means the operator expects activity, so this is an
            # audit record, not an escalation. (Opening an incident here
            # is what produced the flood of empty preliminary incidents.)
            self._notify_verdict(self._verdict_payload(
                w, verdict=ThreatVerdict.INTRUDER, armed=False, extra=extra,
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
            "threat eval cam %d: INTRUDER (%s) — triggers=%s samples=%d "
            "best_no_match_sim=%.2f",
            w.camera_id, how, _trigger_summary(w),
            w.samples_taken, w.best_no_match_sim,
        )
        self._notify_verdict(self._verdict_payload(
            w, verdict=ThreatVerdict.INTRUDER, armed=True, extra=extra,
        ))

    def _dispatch_uncertain(self, w: _Window) -> None:
        # UNCERTAIN = the window closed without a usable face, so we
        # couldn't tell worker from intruder. This is NOT an escalation —
        # it's recorded as a verdict (→ Investigations) only. Opening an
        # incident here is what flooded the Incidents list with empty
        # preliminary packages on busy cameras.
        logger.info(
            "threat eval cam %d: UNCERTAIN — no face visible, triggers=%s "
            "samples=%d",
            w.camera_id, _trigger_summary(w), w.samples_taken,
        )
        payload = self._verdict_payload(w, verdict=ThreatVerdict.UNCERTAIN, armed=True)
        # Cut a pre/post video clip of the entry so an operator can still
        # investigate who walked through even though no face was usable.
        self._export_verdict_clip(w, payload)
        self._notify_verdict(payload)

    def _export_verdict_clip(self, w: _Window, payload: dict[str, Any]) -> None:
        """Write a `_clip_pre_s`+`_clip_post_s` MP4 centered on the window's
        open time (the subject entering) into the verdict's capture dir, so
        it shows up on the Investigations record as video evidence."""
        if self._capture_dir is None:
            return
        recorder = self._recorders.get(w.camera_id)
        if recorder is None or not hasattr(recorder, "export_clip"):
            return
        capture_id = payload.get("capture_id") or uuid.uuid4().hex
        out_dir = self._capture_dir / capture_id
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            recorder.export_clip(
                out_dir / "clip.mp4",
                center_ts=w.opened_at,
                pre_seconds=self._clip_pre_s,
                post_seconds=self._clip_post_s,
            )
            payload["capture_id"] = capture_id
            payload["clip"] = "clip.mp4"
        except Exception:
            logger.exception(
                "threat eval cam %d: verdict clip export failed", w.camera_id,
            )


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
        capture_id, capture_count = self._save_captures(w)
        if capture_id:
            payload["capture_id"] = capture_id
            payload["capture_count"] = capture_count
        return payload

    def _save_captures(self, w: "_Window") -> tuple[Optional[str], int]:
        """Write the window's representative JPEGs to a per-verdict dir and
        return (capture_id, count). The matched frame (if any) leads, then
        the most recent samples — giving the operator the face the engine
        keyed on plus surrounding context."""
        if self._capture_dir is None:
            return None, 0
        jpegs: list[bytes] = []
        # Annotated detection frames first — they contain the subject in
        # the zone, which is what an operator actually wants to see.
        jpegs.extend(list(w.evidence_jpegs))
        if w.best_match_frame is not None:
            best = _encode_capture_jpeg(w.best_match_frame)
            if best:
                jpegs.append(best)
        jpegs.extend(list(w.sample_jpegs))
        if not jpegs:
            return None, 0
        capture_id = uuid.uuid4().hex
        try:
            out_dir = self._capture_dir / capture_id
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, blob in enumerate(jpegs, start=1):
                (out_dir / f"sample_{i:03d}.jpg").write_bytes(blob)
        except OSError:
            logger.exception("threat eval: failed to persist verdict captures")
            return None, 0
        return capture_id, len(jpegs)

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


def _encode_capture_jpeg(frame, max_width: int = 1280) -> Optional[bytes]:
    """Downscaled JPEG for Investigations captures — 4K full-res is slow
    to load and oversized for human review. Distinct from `_encode_jpeg`,
    which stays full-res for face/incident evidence."""
    if frame is None:
        return None
    try:
        import cv2
        h, w = frame.shape[:2]
        if w > max_width:
            scale = max_width / float(w)
            frame = cv2.resize(frame, (max_width, int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes() if ok else None
    except Exception:
        return None
