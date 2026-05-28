"""ThreatEvaluator window state machine — three verdict paths."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np

from itips.runtime.threat_evaluator import ThreatEvaluator, ThreatVerdict


def _frame():
    return np.zeros((180, 320, 3), dtype=np.uint8)


def _result(*, matched: bool, embedding_present: bool,
            similarity: float, person_id: str | None = None,
            full_name: str | None = None):
    return MagicMock(
        matched=matched,
        embedding=np.zeros(512) if embedding_present else None,
        similarity=similarity,
        person_id=person_id,
        full_name=full_name,
    )


def _make_evaluator(*, face_engine, window=0.6, sample=0.05,
                    is_armed_fn=None, holdoff_clear=0.3,
                    escalate_after=0.0, recorders=None, capture_dir=None,
                    clip_pre=15.0, clip_post=15.0):
    alert = MagicMock()
    dahua = MagicMock()
    client = MagicMock()
    client.endpoint.snapshot.return_value = _frame()
    dahua.get.return_value = client
    ev = ThreatEvaluator(
        alert_engine=alert,
        dahua_manager=dahua,
        face_engine=face_engine,
        is_armed_fn=is_armed_fn,
        window_seconds=window,
        sample_interval_s=sample,
        holdoff_clear_seconds=holdoff_clear,
        escalate_after_seconds=escalate_after,
        recorders=recorders,
        capture_dir=capture_dir,
        clip_pre_seconds=clip_pre,
        clip_post_seconds=clip_post,
    )
    return ev, alert


def _wait_until(predicate, timeout: float = 2.0, step: float = 0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def test_authorized_match_emits_personnel_seen_and_no_intruder():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=True, embedding_present=True, similarity=0.82,
        person_id="p-1", full_name="Sam",
    )
    ev, alert = _make_evaluator(face_engine=face_engine)
    ev.start()
    try:
        ev.trigger(camera_id=1, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        assert _wait_until(lambda: alert.handle_personnel_seen.called)
    finally:
        ev.stop()
    alert.handle_face_intruder.assert_not_called()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "p-1"
    assert kw["group_id"] == "threat-evaluator"


def test_intruder_when_face_seen_but_never_matches():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=True, similarity=0.21,
    )
    ev, alert = _make_evaluator(face_engine=face_engine, window=0.4)
    ev.start()
    try:
        ev.trigger(camera_id=2, trigger_kind="sensor:zone-3",
                   initial_frame=_frame())
        assert _wait_until(lambda: alert.handle_face_intruder.called,
                            timeout=3.0)
    finally:
        ev.stop()
    alert.handle_personnel_seen.assert_not_called()
    kw = alert.handle_face_intruder.call_args.kwargs
    assert kw["name"] == "INTRUDER"
    assert "triggers" in kw["details"]
    assert kw["details"]["verdict"] == ThreatVerdict.INTRUDER.value


def test_uncertain_when_no_face_ever_seen_back_to_camera():
    face_engine = MagicMock()
    # embedding=None ⇒ no face in frame, the back-to-camera case
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=False, similarity=0.0,
    )
    ev, alert = _make_evaluator(face_engine=face_engine, window=0.4)
    received: list[dict] = []
    ev.add_verdict_listener(received.append)
    ev.start()
    try:
        ev.trigger(camera_id=3, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: any(p["verdict"] == "uncertain" for p in received),
            timeout=3.0,
        )
    finally:
        ev.stop()
    # UNCERTAIN is recorded as a verdict only (→ Investigations); it must
    # NOT open an incident or fire any alarm — crucially, no INTRUDER for a
    # worker with their back turned.
    alert.handle_face_intruder.assert_not_called()
    alert.handle_personnel_seen.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_not_called()
    verdict = next(p for p in received if p["verdict"] == "uncertain")
    assert verdict["camera_id"] == 3
    assert verdict["alarm_fired"] is False


def test_intruder_suppressed_when_system_disarmed():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=True, similarity=0.18,
    )
    ev, alert = _make_evaluator(
        face_engine=face_engine, window=0.4,
        is_armed_fn=lambda: False,  # hub disarmed
    )
    received: list[dict] = []
    ev.add_verdict_listener(received.append)
    ev.start()
    try:
        ev.trigger(camera_id=4, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: any(p["verdict"] == "intruder" for p in received),
            timeout=3.0,
        )
    finally:
        ev.stop()
    # Disarmed → the intruder observation is recorded as a verdict only;
    # no incident is opened and no alarm fires.
    alert.handle_face_intruder.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_not_called()
    verdict = next(p for p in received if p["verdict"] == "intruder")
    assert verdict["armed"] is False
    assert verdict["alarm_fired"] is False


def test_verdict_listener_receives_all_three_paths():
    """Subscribers (webhook dispatcher, hub) get one payload per closed
    window with verdict ∈ {authorized, intruder, uncertain}."""
    received: list[dict] = []

    def collector(payload):
        received.append(payload)

    # AUTHORIZED
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=True, embedding_present=True, similarity=0.91,
        person_id="p-7", full_name="Alex",
    )
    ev, _ = _make_evaluator(face_engine=face_engine, window=0.4)
    ev.add_verdict_listener(collector)
    ev.start()
    try:
        ev.trigger(camera_id=10, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        assert _wait_until(lambda: any(
            p.get("verdict") == "authorized" for p in received))
    finally:
        ev.stop()
    auth = [p for p in received if p["verdict"] == "authorized"][0]
    assert auth["camera_id"] == 10
    assert auth["person_uid"] == "p-7"
    assert auth["alarm_fired"] is False
    assert auth["armed"] is True

    # INTRUDER (armed)
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=True, similarity=0.22,
    )
    ev2, _ = _make_evaluator(face_engine=face_engine, window=0.3)
    ev2.add_verdict_listener(collector)
    ev2.start()
    try:
        ev2.trigger(camera_id=11, trigger_kind="sensor:zone-2",
                    initial_frame=_frame())
        assert _wait_until(lambda: any(
            p.get("verdict") == "intruder" and p.get("camera_id") == 11
            for p in received), timeout=3.0)
    finally:
        ev2.stop()
    intr = [p for p in received
            if p["verdict"] == "intruder" and p["camera_id"] == 11][0]
    assert intr["alarm_fired"] is True
    assert intr["armed"] is True

    # INTRUDER (disarmed) — verdict still fires, alarm_fired is False
    ev3, _ = _make_evaluator(face_engine=face_engine, window=0.3,
                              is_armed_fn=lambda: False)
    ev3.add_verdict_listener(collector)
    ev3.start()
    try:
        ev3.trigger(camera_id=12, trigger_kind="sensor:zone-9",
                    initial_frame=_frame())
        assert _wait_until(lambda: any(
            p.get("verdict") == "intruder" and p.get("camera_id") == 12
            for p in received), timeout=3.0)
    finally:
        ev3.stop()
    disarmed = [p for p in received
                if p["verdict"] == "intruder" and p["camera_id"] == 12][0]
    assert disarmed["armed"] is False
    assert disarmed["alarm_fired"] is False

    # UNCERTAIN
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=False, similarity=0.0,
    )
    ev4, _ = _make_evaluator(face_engine=face_engine, window=0.3)
    ev4.add_verdict_listener(collector)
    ev4.start()
    try:
        ev4.trigger(camera_id=13, trigger_kind="camera:line_cross",
                    initial_frame=_frame())
        assert _wait_until(lambda: any(
            p.get("verdict") == "uncertain" and p.get("camera_id") == 13
            for p in received), timeout=3.0)
    finally:
        ev4.stop()
    uncertain = [p for p in received
                 if p["verdict"] == "uncertain" and p["camera_id"] == 13][0]
    assert uncertain["alarm_fired"] is False


def _authorized_engine():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=True, embedding_present=True, similarity=0.9,
        person_id="p-1", full_name="Sam",
    )
    return face_engine


def test_authorized_verdict_enters_holdoff():
    ev, _ = _make_evaluator(face_engine=_authorized_engine())
    ev.start()
    try:
        ev.trigger(camera_id=21, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert _wait_until(lambda: 21 in ev._holdoff)
    finally:
        ev.stop()


def test_holdoff_drops_further_triggers():
    """While held off, new triggers open no window and re-fire no verdict."""
    face_engine = _authorized_engine()
    # Long clear window so the hold-off stays up for the whole test.
    ev, alert = _make_evaluator(face_engine=face_engine, holdoff_clear=30.0)
    ev.start()
    try:
        ev.trigger(camera_id=22, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert _wait_until(lambda: 22 in ev._holdoff)
        seen = alert.handle_personnel_seen.call_count
        for _ in range(3):
            ev.trigger(camera_id=22, trigger_kind="camera:face_event",
                       initial_frame=_frame())
        # Give the loop time to (not) act on the dropped triggers.
        assert not _wait_until(
            lambda: alert.handle_personnel_seen.call_count > seen,
            timeout=0.5,
        )
        assert all(w["camera_id"] != 22 for w in ev.active_windows())
    finally:
        ev.stop()


def test_holdoff_lifts_after_frame_clears_then_resumes():
    face_engine = _authorized_engine()
    match = face_engine.recognize.return_value
    ev, alert = _make_evaluator(face_engine=face_engine, holdoff_clear=0.2)
    ev.start()
    try:
        ev.trigger(camera_id=23, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert _wait_until(lambda: 23 in ev._holdoff)
        # Worker leaves the frame: no face from now on → hold-off lifts.
        face_engine.recognize.return_value = _result(
            matched=False, embedding_present=False, similarity=0.0)
        assert _wait_until(lambda: 23 not in ev._holdoff, timeout=3.0)
        # Worker returns and faces the camera → fresh window re-authorizes.
        face_engine.recognize.return_value = match
        before = alert.handle_personnel_seen.call_count
        ev.trigger(camera_id=23, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: alert.handle_personnel_seen.call_count > before,
            timeout=3.0)
    finally:
        ev.stop()


def test_holdoff_face_event_resets_clear_countdown():
    """A face event arriving mid-countdown means the worker is still there,
    so the clear timer resets and the hold-off stays up."""
    face_engine = _authorized_engine()
    # Generous clear window so the countdown can't lift before we assert.
    ev, _ = _make_evaluator(face_engine=face_engine, holdoff_clear=2.0)
    ev.start()
    try:
        ev.trigger(camera_id=24, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert _wait_until(lambda: 24 in ev._holdoff)
        # Frame goes clear, countdown starts.
        face_engine.recognize.return_value = _result(
            matched=False, embedding_present=False, similarity=0.0)
        assert _wait_until(lambda: ev._holdoff.get(24) is not None
                           and ev._holdoff[24].clear_since is not None)
        # A camera face event resets the countdown back to None.
        ev.trigger(camera_id=24, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert ev._holdoff.get(24) is not None
        assert ev._holdoff[24].clear_since is None
    finally:
        ev.stop()


def test_multiple_triggers_collapse_into_one_window():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=False, similarity=0.0,
    )
    ev, _ = _make_evaluator(face_engine=face_engine, window=0.5)
    received: list[dict] = []
    ev.add_verdict_listener(received.append)
    ev.start()
    try:
        ev.trigger(camera_id=5, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        ev.trigger(camera_id=5, trigger_kind="camera:face_detected",
                   initial_frame=_frame())
        ev.trigger(camera_id=5, trigger_kind="sensor:zone-1",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: any(p["camera_id"] == 5 for p in received),
            timeout=3.0,
        )
    finally:
        ev.stop()
    # Three triggers, one collapsed verdict carrying all three.
    verdicts = [p for p in received if p["camera_id"] == 5]
    assert len(verdicts) == 1
    assert len(verdicts[0]["triggers"]) == 3


# ─── dwell-based early escalation ─────────────────────────────────────


def test_dwell_escalates_to_intruder_early():
    """A confirmed stranger (face seen, never matched) who keeps dwelling is
    escalated before the full window expires."""
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=True, similarity=0.2,
    )
    # Long window so it's the escalation, not expiry, that fires.
    ev, alert = _make_evaluator(face_engine=face_engine, window=10.0,
                                sample=0.05, escalate_after=0.3)
    ev.start()
    try:
        ev.trigger(camera_id=30, trigger_kind="behavior:region_intrusion",
                   initial_frame=_frame())
        assert _wait_until(lambda: alert.handle_face_intruder.called, timeout=3.0)
    finally:
        ev.stop()
    kw = alert.handle_face_intruder.call_args.kwargs
    assert kw["details"]["escalated_early"] is True


def test_dwell_escalates_without_a_face():
    """Person-presence: someone who keeps dwelling in the zone is escalated
    even when no usable face is ever captured (back to camera)."""
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=False, similarity=0.0,
    )
    ev, alert = _make_evaluator(face_engine=face_engine, window=10.0,
                                sample=0.05, escalate_after=0.3)
    ev.start()
    try:
        # Re-trigger continuously to simulate a person lingering in the zone
        # (this is what the BehaviorWatcher does at ~2 fps while YOLO sees
        # someone), with no face ever recognised.
        end = time.monotonic() + 2.0
        while time.monotonic() < end and not alert.handle_face_intruder.called:
            ev.trigger(camera_id=31, trigger_kind="behavior:region_intrusion",
                       initial_frame=_frame())
            time.sleep(0.05)
        assert alert.handle_face_intruder.called
    finally:
        ev.stop()
    assert alert.handle_face_intruder.call_args.kwargs["details"]["escalated_early"] is True


# ─── UNCERTAIN evidence clip ──────────────────────────────────────────


class _FakeRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def export_clip(self, out_path, *, center_ts, pre_seconds, post_seconds):
        self.calls.append({
            "out_path": out_path, "center_ts": center_ts,
            "pre": pre_seconds, "post": post_seconds,
        })
        from pathlib import Path
        Path(out_path).write_bytes(b"\x00\x00\x00\x18ftypmp42")  # stub mp4


def test_uncertain_exports_pre_post_clip(tmp_path):
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=False, similarity=0.0,
    )
    rec = _FakeRecorder()
    ev, _ = _make_evaluator(
        face_engine=face_engine, window=0.4, sample=0.05,
        recorders={32: rec}, capture_dir=tmp_path, clip_pre=15, clip_post=15,
    )
    received: list[dict] = []
    ev.add_verdict_listener(received.append)
    ev.start()
    try:
        ev.trigger(camera_id=32, trigger_kind="behavior:region_intrusion",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: any(p["verdict"] == "uncertain" for p in received), timeout=3.0)
    finally:
        ev.stop()
    assert len(rec.calls) == 1
    assert rec.calls[0]["pre"] == 15 and rec.calls[0]["post"] == 15
    verdict = next(p for p in received if p["verdict"] == "uncertain")
    assert verdict["clip"] == "clip.mp4"
    assert (tmp_path / verdict["capture_id"] / "clip.mp4").exists()


# ─── hold-off person accessor (Live "authorized" badge) ───────────────


def test_holdoff_person_reports_authorized_worker():
    ev, _ = _make_evaluator(face_engine=_authorized_engine())
    ev.start()
    try:
        ev.trigger(camera_id=33, trigger_kind="camera:face_event",
                   initial_frame=_frame())
        assert _wait_until(lambda: ev.holdoff_person(33) == "Sam")
        assert ev.holdoff_person(999) is None
    finally:
        ev.stop()
