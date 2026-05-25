"""IoU tracker — greedy matching, age-out, per-camera state."""

from __future__ import annotations

from itips.ml.tracker import Detection, IoUTracker


def _det(x1, y1, x2, y2, cls="person", conf=0.9):
    return Detection(bbox=(x1, y1, x2, y2), class_name=cls, confidence=conf)


# ─── basic matching ─────────────────────────────────────────────────


def test_first_detection_creates_new_track():
    tracker = IoUTracker()
    tracks = tracker.update(camera_id=1, detections=[_det(10, 10, 50, 80)], ts=1.0)
    assert len(tracks) == 1
    assert tracks[0].track_id == 1
    assert tracks[0].update_count == 1


def test_overlapping_detection_extends_same_track():
    tracker = IoUTracker(iou_threshold=0.2)
    tracker.update(1, [_det(10, 10, 50, 80)], ts=1.0)
    tracks = tracker.update(1, [_det(15, 12, 55, 82)], ts=2.0)
    assert len(tracks) == 1
    assert tracks[0].update_count == 2


def test_far_detection_opens_new_track():
    tracker = IoUTracker(iou_threshold=0.2)
    tracker.update(1, [_det(10, 10, 50, 80)], ts=1.0)
    tracks = tracker.update(1, [_det(500, 400, 540, 470)], ts=2.0)
    track_ids = sorted(t.track_id for t in tracks)
    assert track_ids == [1, 2]


def test_class_mismatch_prevents_match():
    """A car detection shouldn't claim a person track even with overlap."""
    tracker = IoUTracker(iou_threshold=0.1)
    tracker.update(1, [_det(10, 10, 100, 100, cls="person")], ts=1.0)
    tracks = tracker.update(1, [_det(10, 10, 100, 100, cls="car")], ts=2.0)
    track_ids = sorted(t.track_id for t in tracks)
    assert track_ids == [1, 2]


def test_per_camera_state_isolated():
    tracker = IoUTracker()
    tracker.update(1, [_det(0, 0, 50, 50)], ts=1.0)
    tracker.update(2, [_det(0, 0, 50, 50)], ts=1.0)
    cam1 = tracker.tracks_for(1)
    cam2 = tracker.tracks_for(2)
    assert len(cam1) == 1 and len(cam2) == 1
    assert cam1[0].track_id != cam2[0].track_id


# ─── age-out ────────────────────────────────────────────────────────


def test_stale_track_dropped_after_max_age():
    tracker = IoUTracker(max_age_s=2.0)
    tracker.update(1, [_det(10, 10, 50, 80)], ts=1.0)
    # Update with no overlap and beyond max_age.
    tracks = tracker.update(1, [_det(500, 500, 540, 570)], ts=10.0)
    track_ids = sorted(t.track_id for t in tracks)
    # Old track should be gone; only the new one survives.
    assert track_ids == [2]


def test_empty_detection_round_does_not_reset_recent_tracks():
    """A frame with no detections keeps the existing track alive until
    `max_age_s` elapses — handy when the person is briefly occluded."""
    tracker = IoUTracker(max_age_s=10.0)
    tracker.update(1, [_det(10, 10, 50, 80)], ts=1.0)
    tracker.update(1, [], ts=2.0)
    tracks = tracker.tracks_for(1)
    assert len(tracks) == 1


# ─── centroid history ─────────────────────────────────────────────


def test_history_records_each_centroid_update():
    tracker = IoUTracker(iou_threshold=0.1)
    tracker.update(1, [_det(0, 0, 100, 100)], ts=1.0)
    tracker.update(1, [_det(10, 10, 110, 110)], ts=2.0)
    tracker.update(1, [_det(20, 20, 120, 120)], ts=3.0)
    [trk] = tracker.tracks_for(1)
    centroids = list(trk.history)
    assert len(centroids) == 3
    # Centroid of (0,0,100,100) is (50,50); next (60,60); (70,70).
    assert centroids[0] == (50.0, 50.0)
    assert centroids[-1] == (70.0, 70.0)


# ─── reset ──────────────────────────────────────────────────────────


def test_reset_clears_camera_state():
    tracker = IoUTracker()
    tracker.update(1, [_det(0, 0, 50, 50)], ts=1.0)
    tracker.update(2, [_det(0, 0, 50, 50)], ts=1.0)
    tracker.reset(1)
    assert tracker.tracks_for(1) == []
    assert len(tracker.tracks_for(2)) == 1
