"""Detect → track → evaluate zones → emit synthetic IVS-equivalent alerts.

Drives the fallback for cameras without native IVS. Alert types match
the strings AlertEngine.handle_behaviour_alert_simple already accepts
(intrusion / loitering / line_crossing).
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


# Re-exported so callers catch the same error type for any engine.
BehaviorEngineUnavailable = ObjectDetectorUnavailable


@dataclass
class BehaviorAlert:
    alert_type: str
    zone_id: str
    zone_name: str
    track_id: int
    class_name: str
    bbox: tuple[float, float, float, float]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BehaviorAnalysis:
    """Returned by `analyse_full()` — adds detections + tracks for the
    ML Lab so an operator can see why no alert fired (YOLO missed?
    person outside every zone? wrong camera?)."""

    alerts: list[BehaviorAlert]
    detections: list[Detection]
    tracks: list[TrackedObject]


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
        preset_state=None,
    ) -> None:
        self._zones = zone_store
        self._detector = object_detector
        self._tracker = tracker or IoUTracker()
        self._loiter_dwell_s = float(loiter_dwell_s)
        self._loiter_min_events = int(loiter_min_events)
        # Used to gate preset-bound zones — a zone drawn at preset "Home"
        # is only meaningful while the camera is at "Home"; for any other
        # orientation the zone overlays a different patch of scene and
        # would produce false alarms.
        self._preset_state = preset_state
        # Per-(camera, track, zone): how long has this track been inside
        # the region zone, and have we already fired its loiter alert?
        self._dwell_state: dict[tuple[int, int, str], _DwellState] = {}
        self._dwell_lock = threading.Lock()

    def warmup_async(self) -> None:
        self._detector.warmup_async()

    def is_ready(self) -> bool:
        return self._detector.is_ready()

    def analyse(
        self,
        camera_id: int,
        frame: "np.ndarray",
        ts: Optional[float] = None,
    ) -> list[BehaviorAlert]:
        """Production path. Skips detection when the camera has no
        zones, to save the GPU on noisy VideoMotion events."""
        zones = self._active_zones(camera_id)
        if not zones:
            return []
        return self.analyse_full(camera_id, frame, ts=ts).alerts

    def _active_zones(self, camera_id: int):
        """Filter the camera's zones down to those that should fire
        right now — preset-bound zones only count when the camera is
        currently at that preset (per `PresetStateTracker`); zones
        with no preset binding are always active."""
        zones = self._zones.for_camera(camera_id)
        if not zones:
            return []
        if self._preset_state is None:
            return zones
        current = self._preset_state.current(camera_id)
        active = []
        for z in zones:
            bound = getattr(z, "preset_name", None)
            if bound is None or (current is not None and bound == current):
                active.append(z)
        return active

    def analyse_full(
        self,
        camera_id: int,
        frame: "np.ndarray",
        ts: Optional[float] = None,
    ) -> BehaviorAnalysis:
        """Always runs the detector — even with no zones — so the ML
        Lab can surface what YOLO saw."""
        ts = float(ts) if ts is not None else time.time()
        zones = self._active_zones(camera_id)

        detections = self._detector.detect(frame)
        if not detections:
            return BehaviorAnalysis(alerts=[], detections=[], tracks=[])

        h, w = frame.shape[:2]
        tracks = self._tracker.update(camera_id, detections, ts)

        alerts: list[BehaviorAlert] = []
        if zones:
            for trk in tracks:
                # Skip stale carry-over tracks (no detection this frame).
                if abs(trk.last_seen_ts - ts) > 1e-3:
                    continue
                for zone in zones:
                    if zone.zone_type == ZONE_TYPE_REGION:
                        alerts.extend(self._evaluate_region(camera_id, trk, zone, ts, w, h))
                    elif zone.zone_type == ZONE_TYPE_LINE:
                        alerts.extend(self._evaluate_line(trk, zone, w, h))
            self._gc_dwell(camera_id, alive_tracks=tracks)

        return BehaviorAnalysis(alerts=alerts, detections=detections, tracks=tracks)

    def _evaluate_region(
        self,
        camera_id: int,
        trk: TrackedObject,
        zone: Zone,
        ts: float,
        w: int,
        h: int,
    ) -> list[BehaviorAlert]:
        # Anchor at the bbox bottom-center (feet), not the centroid —
        # ground zones drawn at floor level miss the centroid (hip level).
        x1, _y1, x2, y2 = trk.bbox
        ax, ay = (x1 + x2) / 2.0, y2
        nx, ny = ax / max(1, w), ay / max(1, h)
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

    def _evaluate_line(
        self,
        trk: TrackedObject,
        zone: Zone,
        w: int,
        h: int,
    ) -> list[BehaviorAlert]:
        if len(trk.history) < 2:
            return []
        prev_px, curr_px = trk.history[-2], trk.history[-1]
        prev = (prev_px[0] / max(1, w), prev_px[1] / max(1, h))
        curr = (curr_px[0] / max(1, w), curr_px[1] / max(1, h))

        out: list[BehaviorAlert] = []
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
            break   # one crossing per analyse() per zone
        return out

    def _clear_dwell(self, camera_id: int, track_id: int, zone_id: str) -> None:
        with self._dwell_lock:
            self._dwell_state.pop((camera_id, track_id, zone_id), None)

    def _gc_dwell(self, camera_id: int, alive_tracks: list[TrackedObject]) -> None:
        alive_ids = {t.track_id for t in alive_tracks}
        with self._dwell_lock:
            stale = [
                k for k in self._dwell_state
                if k[0] == camera_id and k[1] not in alive_ids
            ]
            for k in stale:
                self._dwell_state.pop(k, None)


@dataclass
class _DwellState:
    first_seen_ts: float
    events_inside: int
    alerted: bool
    last_seen_ts: float = 0.0


def _crossing_direction(
    prev: tuple[float, float], curr: tuple[float, float],
    seg_a: tuple[float, float], seg_b: tuple[float, float],
) -> str:
    """Matches Dahua's "LeftToRight" / "RightToLeft" — screen-relative,
    not line-relative. y-axis is flipped (down), so cross_prev > 0
    means P is to the screen-left of the line vector."""
    lx, ly = seg_b[0] - seg_a[0], seg_b[1] - seg_a[1]
    cross_prev = lx * (prev[1] - seg_a[1]) - ly * (prev[0] - seg_a[0])
    cross_curr = lx * (curr[1] - seg_a[1]) - ly * (curr[0] - seg_a[0])
    if cross_prev > 0 and cross_curr < 0:
        return "LeftToRight"
    if cross_prev < 0 and cross_curr > 0:
        return "RightToLeft"
    return "Any"
