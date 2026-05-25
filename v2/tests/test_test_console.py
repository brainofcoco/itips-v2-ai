"""Tests for the Test Console primitives.

Covers:
* EventTap ring buffer + cursor semantics.
* `_simulate` correctly routes each event_type onto the right AlertEngine
  handler.
"""

from __future__ import annotations

import time

from itips.api.dashboard import _simulate
from itips.runtime.event_tap import EventTap


class _RecordingEngine:
    """Captures every handler call so the test can assert on it."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def handle_face_intruder(self, **kw):
        self.calls.append(("face_intruder", kw))

    def handle_personnel_seen(self, **kw):
        self.calls.append(("personnel_seen", kw))

    def handle_behaviour_alert_simple(self, **kw):
        self.calls.append(("behaviour", kw))

    def handle_plate_capture(self, **kw):
        self.calls.append(("plate", kw))

    def handle_fire(self, **kw):
        self.calls.append(("fire", kw))

    def handle_smoke(self, **kw):
        self.calls.append(("smoke", kw))


# ─── EventTap ─────────────────────────────────────────────────────────


def test_event_tap_returns_only_new_events_per_cursor():
    tap = EventTap(capacity=10)
    tap.publish(camera_id=1, code="VideoMotion", action="Start", index=0, data={})
    tap.publish(camera_id=1, code="VideoMotion", action="Stop", index=0, data={})

    items, cursor1 = tap.since(0)
    assert len(items) == 2
    assert items[0]["action"] == "Start"
    assert items[1]["action"] == "Stop"

    # Polling again with the latest cursor returns nothing.
    items2, cursor2 = tap.since(cursor1)
    assert items2 == []
    assert cursor2 == cursor1

    # New publish, only the new one returns.
    tap.publish(camera_id=2, code="FaceRecognition", action="Pulse", index=0, data={"Face": {}})
    items3, _ = tap.since(cursor2)
    assert len(items3) == 1
    assert items3[0]["code"] == "FaceRecognition"
    assert items3[0]["camera_id"] == 2


def test_event_tap_capacity_drops_oldest():
    tap = EventTap(capacity=3)
    for i in range(5):
        tap.publish(camera_id=1, code="X", action="Pulse", index=i, data={})
    recent = tap.recent()
    assert len(recent) == 3
    # Oldest two dropped; remaining indices should be 2, 3, 4.
    assert [r["index"] for r in recent] == [2, 3, 4]


def test_event_tap_has_jpeg_flag():
    tap = EventTap(capacity=5)
    tap.publish(camera_id=1, code="FaceRecognition", action="Pulse",
                index=0, data={}, has_jpeg=True)
    tap.publish(camera_id=1, code="CrossLineDetection", action="Start",
                index=0, data={}, has_jpeg=False)
    items, _ = tap.since(0)
    assert items[0]["has_jpeg"] is True
    assert items[1]["has_jpeg"] is False


def test_event_tap_attaches_timestamp():
    tap = EventTap(capacity=5)
    before = time.time()
    tap.publish(camera_id=1, code="X", action="Pulse", index=0, data={})
    after = time.time()
    items, _ = tap.since(0)
    assert before <= items[0]["ts"] <= after


# ─── Simulator dispatch ───────────────────────────────────────────────


def test_simulator_face_intruder():
    engine = _RecordingEngine()
    _simulate("face_intruder", engine, 1, {"bbox": [10, 20, 30, 40]})
    kind, kw = engine.calls[0]
    assert kind == "face_intruder"
    assert kw["camera_id"] == 1
    assert kw["name"] == "INTRUDER"
    assert tuple(kw["face_bbox"]) == (10, 20, 30, 40)


def test_simulator_face_known():
    engine = _RecordingEngine()
    _simulate("face_known", engine, 2,
              {"person_uid": "0123", "name": "Alpha", "similarity": 92})
    kind, kw = engine.calls[0]
    assert kind == "personnel_seen"
    assert kw["person_uid"] == "0123"
    assert kw["name"] == "Alpha"
    assert kw["similarity"] == 92


def test_simulator_behaviour_types():
    engine = _RecordingEngine()
    for et in ("line_crossing", "intrusion", "loitering"):
        _simulate(et, engine, 1, {})
    types = [kw["alert_type"] for kind, kw in engine.calls if kind == "behaviour"]
    assert types == ["line_crossing", "intrusion", "loitering"]


def test_simulator_plate():
    engine = _RecordingEngine()
    _simulate("plate", engine, 3, {"plate_number": "ABC-123"})
    kind, kw = engine.calls[0]
    assert kind == "plate"
    assert kw["plate_number"] == "ABC-123"
    assert kw["camera_id"] == 3


def test_simulator_fire_and_smoke():
    engine = _RecordingEngine()
    _simulate("fire", engine, 1, {})
    _simulate("smoke", engine, 1, {"color": "Red"})
    kinds = [kind for kind, _ in engine.calls]
    assert kinds == ["fire", "smoke"]
    assert engine.calls[1][1]["details"]["color"] == "Red"


def test_simulator_unknown_raises():
    engine = _RecordingEngine()
    import pytest
    with pytest.raises(KeyError):
        _simulate("nope", engine, 1, {})
