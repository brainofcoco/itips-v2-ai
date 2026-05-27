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
                    is_armed_fn=None):
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
    ev.start()
    try:
        ev.trigger(camera_id=3, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: alert.handle_behaviour_alert_simple.called,
            timeout=3.0,
        )
    finally:
        ev.stop()
    # Crucial: no INTRUDER alarm fired for a worker with their back turned.
    alert.handle_face_intruder.assert_not_called()
    alert.handle_personnel_seen.assert_not_called()
    kw = alert.handle_behaviour_alert_simple.call_args.kwargs
    assert kw["alert_type"] == "threat_uncertain"


def test_intruder_suppressed_when_system_disarmed():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=True, similarity=0.18,
    )
    ev, alert = _make_evaluator(
        face_engine=face_engine, window=0.4,
        is_armed_fn=lambda: False,  # hub disarmed
    )
    ev.start()
    try:
        ev.trigger(camera_id=4, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: alert.handle_behaviour_alert_simple.called,
            timeout=3.0,
        )
    finally:
        ev.stop()
    alert.handle_face_intruder.assert_not_called()
    kw = alert.handle_behaviour_alert_simple.call_args.kwargs
    assert kw["alert_type"] == "threat_observation_disarmed"


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


def test_multiple_triggers_collapse_into_one_window():
    face_engine = MagicMock()
    face_engine.recognize.return_value = _result(
        matched=False, embedding_present=False, similarity=0.0,
    )
    ev, alert = _make_evaluator(face_engine=face_engine, window=0.5)
    ev.start()
    try:
        ev.trigger(camera_id=5, trigger_kind="camera:line_cross",
                   initial_frame=_frame())
        ev.trigger(camera_id=5, trigger_kind="camera:face_detected",
                   initial_frame=_frame())
        ev.trigger(camera_id=5, trigger_kind="sensor:zone-1",
                   initial_frame=_frame())
        assert _wait_until(
            lambda: alert.handle_behaviour_alert_simple.called,
            timeout=3.0,
        )
    finally:
        ev.stop()
    # Three triggers, one verdict.
    assert alert.handle_behaviour_alert_simple.call_count == 1
    triggers = alert.handle_behaviour_alert_simple.call_args.kwargs["details"]["triggers"]
    assert len(triggers) == 3
