"""BehaviorEngine — synthesises IVS-equivalent alerts from detections.

Real YOLO is never loaded. A fake ObjectDetector returns scripted
detection lists per `analyse()` call, so we can test:

  * region zones → `intrusion` fires while a person is inside
  * region zones → `loitering` fires after dwell + min_events
  * line zones → `line_crossing` fires when the centroid segment
    crosses the line, respecting direction filter
"""

from __future__ import annotations

import numpy as np

from itips.ml.behavior_engine import BehaviorEngine
from itips.ml.tracker import Detection, IoUTracker
from itips.ml.zone_store import (
    ZONE_TYPE_LINE,
    ZONE_TYPE_REGION,
    Zone,
    ZoneStore,
)


class _ScriptedDetector:
    """Stand-in for ObjectDetector. Returns a queued list per call."""

    def __init__(self, sequence: list[list[Detection]]):
        self._seq = list(sequence)
        self._ready = True

    def detect(self, frame):
        if not self._seq:
            return []
        return self._seq.pop(0)

    def warmup_async(self):
        return

    def is_ready(self) -> bool:
        return True


def _frame(w: int = 640, h: int = 480) -> np.ndarray:
    return np.zeros((h, w, 3), dtype="uint8")


def _person_at(cx: float, cy: float, w: float = 40, h: float = 80) -> Detection:
    """Detection centred at (cx, cy) — bbox derived from half-width/height.

    Tests that need IoU continuity across larger movement should pass
    larger `w`/`h` so consecutive bboxes still overlap enough for the
    tracker to match them as one person.
    """
    return Detection(
        bbox=(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
        class_name="person",
        confidence=0.9,
    )


def _make_engine(tmp_path, sequence, *, loiter_dwell_s=15.0, loiter_min_events=3):
    zone_store = ZoneStore(path=tmp_path / "zones.json")
    detector = _ScriptedDetector(sequence)
    engine = BehaviorEngine(
        zone_store=zone_store,
        object_detector=detector,
        loiter_dwell_s=loiter_dwell_s,
        loiter_min_events=loiter_min_events,
        tracker=IoUTracker(iou_threshold=0.1, max_age_s=30.0),
    )
    return engine, zone_store


# ─── region zones: intrusion + loitering ────────────────────────────


def test_no_zones_skips_detection_entirely(tmp_path):
    """If no zone is configured for the camera, analyse() short-circuits."""
    engine, _ = _make_engine(tmp_path, sequence=[[_person_at(320, 240)]])
    alerts = engine.analyse(camera_id=1, frame=_frame(), ts=1.0)
    assert alerts == []


def test_person_inside_region_fires_intrusion(tmp_path):
    engine, zones = _make_engine(tmp_path, sequence=[[_person_at(320, 240)]])
    zones.upsert_zone(1, Zone(
        zone_id="compound", zone_type=ZONE_TYPE_REGION,
        # Covers the centre of the frame.
        points=[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
        name="Compound",
    ))
    alerts = engine.analyse(1, _frame(), ts=1.0)
    types = [a.alert_type for a in alerts]
    assert "intrusion" in types
    intrusion = next(a for a in alerts if a.alert_type == "intrusion")
    assert intrusion.zone_id == "compound"
    assert intrusion.class_name == "person"


def test_person_outside_region_emits_nothing(tmp_path):
    engine, zones = _make_engine(tmp_path, sequence=[[_person_at(10, 10)]])
    zones.upsert_zone(1, Zone(
        zone_id="compound", zone_type=ZONE_TYPE_REGION,
        points=[(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)],
        name="Compound",
    ))
    assert engine.analyse(1, _frame(), ts=1.0) == []


def test_loitering_fires_after_dwell_and_min_events(tmp_path):
    # Three consecutive detections inside the zone, far apart in time.
    seq = [[_person_at(320, 240)]] * 3
    engine, zones = _make_engine(tmp_path, seq,
                                  loiter_dwell_s=10.0, loiter_min_events=3)
    zones.upsert_zone(1, Zone(
        zone_id="z", zone_type=ZONE_TYPE_REGION,
        points=[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
    ))
    alerts1 = engine.analyse(1, _frame(), ts=1.0)
    alerts2 = engine.analyse(1, _frame(), ts=6.0)
    alerts3 = engine.analyse(1, _frame(), ts=12.0)
    types1 = [a.alert_type for a in alerts1]
    types2 = [a.alert_type for a in alerts2]
    types3 = [a.alert_type for a in alerts3]
    assert "loitering" not in types1
    assert "loitering" not in types2
    assert "loitering" in types3


def test_loiter_alerts_only_once_per_track(tmp_path):
    """Once we've declared a loiter, we shouldn't re-fire on every
    subsequent frame the person stays inside the zone."""
    seq = [[_person_at(320, 240)]] * 5
    engine, zones = _make_engine(tmp_path, seq,
                                  loiter_dwell_s=5.0, loiter_min_events=2)
    zones.upsert_zone(1, Zone(
        zone_id="z", zone_type=ZONE_TYPE_REGION,
        points=[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
    ))
    fired = 0
    for t in [1.0, 2.0, 7.0, 8.0, 9.0]:
        for a in engine.analyse(1, _frame(), ts=t):
            if a.alert_type == "loitering":
                fired += 1
    assert fired == 1


# ─── line zones: line crossing ──────────────────────────────────────


def test_line_crossing_fires_when_segment_intersects(tmp_path):
    # Wide bboxes so the IoU tracker keeps them as one track across
    # the 200-px centroid movement. Centroid 200→400 crosses x=320
    # (which is x=0.5 in a 640-wide frame).
    seq = [
        [_person_at(200, 240, w=300)],   # nx ≈ 0.31
        [_person_at(400, 240, w=300)],   # nx ≈ 0.63
    ]
    engine, zones = _make_engine(tmp_path, seq)
    zones.upsert_zone(1, Zone(
        zone_id="fence", zone_type=ZONE_TYPE_LINE,
        points=[(0.5, 0.0), (0.5, 1.0)],
        direction="Any",
    ))
    engine.analyse(1, _frame(), ts=1.0)  # first detection — no history yet
    alerts = engine.analyse(1, _frame(), ts=2.0)
    types = [a.alert_type for a in alerts]
    assert "line_crossing" in types


def test_line_crossing_respects_direction_filter(tmp_path):
    # Person moves right-to-left across the line.
    seq = [
        [_person_at(400, 240, w=300)],
        [_person_at(200, 240, w=300)],
    ]
    engine, zones = _make_engine(tmp_path, seq)
    zones.upsert_zone(1, Zone(
        zone_id="fence", zone_type=ZONE_TYPE_LINE,
        points=[(0.5, 0.0), (0.5, 1.0)],
        direction="LeftToRight",  # but person is moving R-to-L
    ))
    engine.analyse(1, _frame(), ts=1.0)
    alerts = engine.analyse(1, _frame(), ts=2.0)
    assert all(a.alert_type != "line_crossing" for a in alerts)


def test_line_crossing_does_not_fire_when_far_from_line(tmp_path):
    # Both detections sit on the same side.
    seq = [
        [_person_at(100, 240)],
        [_person_at(150, 240)],
    ]
    engine, zones = _make_engine(tmp_path, seq)
    zones.upsert_zone(1, Zone(
        zone_id="fence", zone_type=ZONE_TYPE_LINE,
        points=[(0.5, 0.0), (0.5, 1.0)],
    ))
    engine.analyse(1, _frame(), ts=1.0)
    alerts = engine.analyse(1, _frame(), ts=2.0)
    assert all(a.alert_type != "line_crossing" for a in alerts)
