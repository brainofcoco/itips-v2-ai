"""Behavioral fallback orchestrator — synthesises IVS-equivalent alerts.

Called from `event_worker._handle_motion` when the capability router
says the camera has no IVS rules deployed. Pipeline:

    frame  →  ObjectDetector  →  IoUTracker (per-camera)  →  ZoneStore
                                       │
                                       ▼
                                BehaviorEngine.analyse()
                                       │
                                       ▼
                          list[BehaviorAlert] → AlertEngine.handle_behaviour_alert_simple

Alert types match the native Dahua events the AlertEngine already
knows how to consume:

  * `intrusion`     — person bbox centroid is inside a region zone
                       on this single frame. Fires every analyse()
                       while the person stays inside the zone.
  * `loitering`     — person tracked inside the same region zone for
                       at least `loiter_dwell_s` seconds across at
                       least `loiter_min_events` consecutive analyses.
                       Fires once per (track, zone) until the track
                       leaves.
  * `line_crossing` — the segment between this track's previous and
                       current centroid crosses a configured line
                       zone (CCW segment-intersection check).

The engine does no I/O itself — `analyse()` is pure compute given a
frame plus shared state. That makes it cheap to unit-test (mock the
detector) and trivial to call from any handler that already has a
frame in hand.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from itips.ml.object_detector import ObjectDetector, ObjectDetectorUnavailable
from itips.ml.tracker import Detection, IoUTracker, TrackedObject
from itips.ml.zone_store import (
    ZONE_TYPE_LINE,
    ZONE_TYPE_REGION,
    Zone,
    ZoneStore,
    point_in_polygon,
    segments_intersect,
)

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


# Re-export so callers can `except BehaviorEngineUnavailable` symmetrically
# with the other engines' error types.
BehaviorEngineUnavailable = ObjectDetectorUnavailable


@dataclass
class BehaviorAlert:
    """One synthesised IVS-equivalent alert.

    `alert_type` maps 1:1 onto the strings `AlertEngine.handle_
    behaviour_alert_simple` already accepts, so routing is a trivial
    string pass-through.
    """

    alert_type: str
    zone_id: str
    zone_name: str
    track_id: int
    class_name: str
    bbox: tuple[float, float, float, float]
    details: dict[str, Any] = field(default_factory=dict)


class BehaviorEngine:
    """Detect → track → evaluate zones → emit synthetic IVS alerts."""

    def __init__(
        self,
        zone_store: ZoneStore,
        object_detector: ObjectDetector,
        *,
        loiter_dwell_s: float = 15.0,
        loiter_min_events: int = 3,
        tracker: Optional[IoUTracker] = None,
    ) -> None:
        self._zones = zone_store
        self._detector = object_detector
        self._tracker = tracker or IoUTracker()
        self._loiter_dwell_s = float(loiter_dwell_s)
        self._loiter_min_events = int(loiter_min_events)
        # Per-(camera, track, zone): how long has this track been inside
        # the region zone, and have we already fired its loiter alert?
        self._dwell_state: dict[tuple[int, int, str], _DwellState] = {}
        self._dwell_lock = threading.Lock()

    # ─── lifecycle pass-through ───────────────────────────────────────

    def warmup_async(self) -> None:
        self._detector.warmup_async()

    def is_ready(self) -> bool:
        return self._detector.is_ready()

    # ─── main entry ───────────────────────────────────────────────────

    def analyse(
        self,
        camera_id: int,
        frame: "np.ndarray",
        ts: Optional[float] = None,
    ) -> list[BehaviorAlert]:
        ts = float(ts) if ts is not None else time.time()
        zones = self._zones.for_camera(camera_id)
        if not zones:
            # Nothing to evaluate — skip detection entirely to save GPU.
            return []

        detections = self._detector.detect(frame)
        if not detections:
            return []

        h, w = frame.shape[:2]
        tracks = self._tracker.update(camera_id, detections, ts)

        alerts: list[BehaviorAlert] = []
        for trk in tracks:
            # Tracks that didn't get a fresh detection on this frame
            # shouldn't trigger — they're stale carry-overs the tracker
            # keeps around for one or two more updates in case of a
            # brief miss.
            if abs(trk.last_seen_ts - ts) > 1e-3:
                continue
            for zone in zones:
                if zone.zone_type == ZONE_TYPE_REGION:
                    alerts.extend(self._evaluate_region(camera_id, trk, zone, ts, w, h))
                elif zone.zone_type == ZONE_TYPE_LINE:
                    alerts.extend(self._evaluate_line(trk, zone, w, h))

        # Stale dwell entries (track left the zone or the tracker
        # dropped it) need cleanup so we re-fire loitering if the same
        # person comes back later.
        self._gc_dwell(camera_id, alive_tracks=tracks)
        return alerts

    # ─── region zones: intrusion + loitering ──────────────────────────

    def _evaluate_region(
        self,
        camera_id: int,
        trk: TrackedObject,
        zone: Zone,
        ts: float,
        w: int,
        h: int,
    ) -> list[BehaviorAlert]:
        # Convert track centroid (in pixels) to the zone's normalised
        # coord space.
        cx, cy = trk.centroid
        nx, ny = cx / max(1, w), cy / max(1, h)
        inside = point_in_polygon(nx, ny, zone.points)
        if not inside:
            self._clear_dwell(camera_id, trk.track_id, zone.zone_id)
            return []

        out: list[BehaviorAlert] = []
        out.append(BehaviorAlert(
            alert_type="intrusion",
            zone_id=zone.zone_id,
            zone_name=zone.name,
            track_id=trk.track_id,
            class_name=trk.class_name,
            bbox=trk.bbox,
            details={"rule_name": zone.name or zone.zone_id,
                     "action": "Inside"},
        ))

        # Loitering bookkeeping.
        key = (camera_id, trk.track_id, zone.zone_id)
        with self._dwell_lock:
            state = self._dwell_state.get(key)
            if state is None:
                state = _DwellState(first_seen_ts=ts, events_inside=1, alerted=False)
                self._dwell_state[key] = state
            else:
                state.events_inside += 1
                state.last_seen_ts = ts
                dwelt = ts - state.first_seen_ts
                if (not state.alerted
                        and dwelt >= self._loiter_dwell_s
                        and state.events_inside >= self._loiter_min_events):
                    state.alerted = True
                    out.append(BehaviorAlert(
                        alert_type="loitering",
                        zone_id=zone.zone_id,
                        zone_name=zone.name,
                        track_id=trk.track_id,
                        class_name=trk.class_name,
                        bbox=trk.bbox,
                        details={
                            "rule_name": zone.name or zone.zone_id,
                            "dwell_seconds": round(dwelt, 2),
                            "events_inside": state.events_inside,
                        },
                    ))
        return out

    # ─── line zones: line crossing ────────────────────────────────────

    def _evaluate_line(
        self,
        trk: TrackedObject,
        zone: Zone,
        w: int,
        h: int,
    ) -> list[BehaviorAlert]:
        if len(trk.history) < 2:
            return []  # need a previous centroid to draw a segment from
        prev_px, curr_px = trk.history[-2], trk.history[-1]
        # Normalise both centroids.
        prev = (prev_px[0] / max(1, w), prev_px[1] / max(1, h))
        curr = (curr_px[0] / max(1, w), curr_px[1] / max(1, h))

        out: list[BehaviorAlert] = []
        # Walk segment-by-segment through the line zone (most zones
        # are 2-point lines but the schema allows polylines).
        pts = zone.points
        for i in range(len(pts) - 1):
            seg_a, seg_b = pts[i], pts[i + 1]
            if not segments_intersect(prev, curr, seg_a, seg_b):
                continue
            direction = _crossing_direction(prev, curr, seg_a, seg_b)
            if zone.direction != "Any" and zone.direction != direction:
                continue
            out.append(BehaviorAlert(
                alert_type="line_crossing",
                zone_id=zone.zone_id,
                zone_name=zone.name,
                track_id=trk.track_id,
                class_name=trk.class_name,
                bbox=trk.bbox,
                details={
                    "rule_name": zone.name or zone.zone_id,
                    "direction": direction,
                },
            ))
            break  # one crossing per analyse() pass per zone
        return out

    # ─── dwell state plumbing ─────────────────────────────────────────

    def _clear_dwell(self, camera_id: int, track_id: int, zone_id: str) -> None:
        with self._dwell_lock:
            self._dwell_state.pop((camera_id, track_id, zone_id), None)

    def _gc_dwell(self, camera_id: int, alive_tracks: list[TrackedObject]) -> None:
        """Drop dwell entries whose tracks are no longer in the live set."""
        alive_ids = {t.track_id for t in alive_tracks}
        with self._dwell_lock:
            stale = [
                k for k in self._dwell_state
                if k[0] == camera_id and k[1] not in alive_ids
            ]
            for k in stale:
                self._dwell_state.pop(k, None)


# ─── helpers ─────────────────────────────────────────────────────────


@dataclass
class _DwellState:
    """Per-(camera, track, zone) loitering bookkeeping."""

    first_seen_ts: float
    events_inside: int
    alerted: bool
    last_seen_ts: float = 0.0


def _crossing_direction(
    prev: tuple[float, float], curr: tuple[float, float],
    seg_a: tuple[float, float], seg_b: tuple[float, float],
) -> str:
    """Direction of line crossing, matching Dahua's `Direction` strings.

    The convention is the one operators draw: with the line going
    seg_a → seg_b in image space (y-axis down), `LeftToRight` means
    the centroid moved from the line's left side to its right side
    in screen coordinates.

    Cross product sign convention here: with screen-y flipped, the
    formula `lx*(P.y - A.y) - ly*(P.x - A.x)` gives a NEGATIVE value
    when P is to the screen-right of the line vector (vertical line
    going top-to-bottom in screen). So `cross_prev > 0 → screen-left`
    and `cross_prev < 0 → screen-right`.
    """
    lx, ly = seg_b[0] - seg_a[0], seg_b[1] - seg_a[1]
    cross_prev = lx * (prev[1] - seg_a[1]) - ly * (prev[0] - seg_a[0])
    cross_curr = lx * (curr[1] - seg_a[1]) - ly * (curr[0] - seg_a[0])
    if cross_prev > 0 and cross_curr < 0:
        return "LeftToRight"
    if cross_prev < 0 and cross_curr > 0:
        return "RightToLeft"
    return "Any"
