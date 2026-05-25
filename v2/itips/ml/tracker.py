"""Lightweight per-camera IoU tracker for the behavioral fallback.

Continuous-streaming trackers (ByteTrack, SORT) assume frames arrive
at ~10 FPS. Our event-driven fallback gets a snapshot only when the
camera fires `VideoMotion` (or similar), which can be 1 to several
seconds apart. That gap is too long for motion-prediction trackers,
so we keep the matching primitive — greedy IoU — and drop everything
fancy.

Design choices that come straight from the gap:

  * **No motion model.** No Kalman filter. Track centroid moves
    exactly where the next detection lands.
  * **History is short.** We keep the last few centroids per track
    only for line-crossing checks (need the previous position to
    test segment intersection).
  * **Age-out by wall-clock, not by frames.** A track is dropped
    after `max_age_s` seconds without a matching detection — frames
    don't tick at a fixed rate here.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """One bbox + class from the object detector."""

    bbox: tuple[float, float, float, float]  # (x1, y1, x2, y2) in pixels
    class_name: str
    confidence: float


@dataclass
class TrackedObject:
    track_id: int
    bbox: tuple[float, float, float, float]
    class_name: str
    centroid: tuple[float, float]
    confidence: float
    first_seen_ts: float
    last_seen_ts: float
    update_count: int = 1
    # Recent centroids (most-recent last) — enough for line-crossing
    # which only needs the previous-vs-current segment.
    history: deque = field(default_factory=lambda: deque(maxlen=5))

    @property
    def age_s(self) -> float:
        return self.last_seen_ts - self.first_seen_ts


class IoUTracker:
    """Per-camera greedy IoU tracker with wall-clock age-out."""

    def __init__(
        self,
        *,
        iou_threshold: float = 0.25,
        max_age_s: float = 6.0,
    ) -> None:
        self._iou_threshold = float(iou_threshold)
        self._max_age_s = float(max_age_s)
        self._tracks_by_camera: dict[int, list[TrackedObject]] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    # ─── public API ───────────────────────────────────────────────────

    def update(
        self,
        camera_id: int,
        detections: list[Detection],
        ts: float,
    ) -> list[TrackedObject]:
        """Assign detections to tracks. Returns live tracks for `camera_id`."""
        with self._lock:
            tracks = self._tracks_by_camera.setdefault(camera_id, [])
            # Drop stale tracks before matching so we don't compare
            # against ghosts.
            tracks = [t for t in tracks if (ts - t.last_seen_ts) <= self._max_age_s]

            unmatched_dets: list[Detection] = []
            consumed_tracks: set[int] = set()

            # Greedy IoU match — for each detection, take the best
            # available track. Sort detections by confidence so
            # the strongest ones win the best matches.
            sorted_dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
            for det in sorted_dets:
                best_idx, best_iou = -1, 0.0
                for i, trk in enumerate(tracks):
                    if i in consumed_tracks:
                        continue
                    if trk.class_name != det.class_name:
                        continue
                    iou = _iou(det.bbox, trk.bbox)
                    if iou > best_iou:
                        best_iou, best_idx = iou, i
                if best_iou >= self._iou_threshold and best_idx >= 0:
                    self._extend_track(tracks[best_idx], det, ts)
                    consumed_tracks.add(best_idx)
                else:
                    unmatched_dets.append(det)

            for det in unmatched_dets:
                tracks.append(self._new_track(det, ts))

            self._tracks_by_camera[camera_id] = tracks
            return list(tracks)

    def tracks_for(self, camera_id: int) -> list[TrackedObject]:
        with self._lock:
            return list(self._tracks_by_camera.get(camera_id, []))

    def reset(self, camera_id: Optional[int] = None) -> None:
        """Drop all tracks (for the camera, or every camera)."""
        with self._lock:
            if camera_id is None:
                self._tracks_by_camera.clear()
            else:
                self._tracks_by_camera.pop(camera_id, None)

    # ─── internals ────────────────────────────────────────────────────

    def _new_track(self, det: Detection, ts: float) -> TrackedObject:
        track_id = self._next_id
        self._next_id += 1
        centroid = _centroid(det.bbox)
        history: deque = deque(maxlen=5)
        history.append(centroid)
        return TrackedObject(
            track_id=track_id,
            bbox=det.bbox,
            class_name=det.class_name,
            centroid=centroid,
            confidence=det.confidence,
            first_seen_ts=ts,
            last_seen_ts=ts,
            history=history,
        )

    def _extend_track(self, trk: TrackedObject, det: Detection, ts: float) -> None:
        trk.bbox = det.bbox
        trk.centroid = _centroid(det.bbox)
        trk.confidence = det.confidence
        trk.last_seen_ts = ts
        trk.update_count += 1
        trk.history.append(trk.centroid)


# ─── helpers ─────────────────────────────────────────────────────────


def _centroid(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, inter_x2 - inter_x1), max(0.0, inter_y2 - inter_y1)
    if iw == 0 or ih == 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0
