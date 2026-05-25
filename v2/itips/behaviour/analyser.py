"""Behaviour analyser — zone-aware rules over ByteTrack-tracked people.

This is a slim re-implementation of V1's behaviour analysis (which was
~1100 lines in a single file). V2 splits zone storage, track state, and
rule evaluation into separate modules. Each rule is short, named, and
unit-testable.

The analyser is per-camera — instantiate one per CameraWorker. State is
not shared between cameras; cross-camera association is a Phase 1 task.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from config.settings import settings
from itips.behaviour.tracks import TrackedPerson, TrackedVehicle
from itips.behaviour.zones import get_store
from itips.utils.geometry import point_in_polygon

logger = logging.getLogger(__name__)


@dataclass
class BehaviourAlert:
    alert_type: str
    camera_id: int
    track_id: int
    details: dict[str, Any]


class BehaviourAnalyser:
    def __init__(self, camera_id: int) -> None:
        self.camera_id = camera_id
        self._tracks: dict[int, TrackedPerson] = {}
        self._vehicles: dict[int, TrackedVehicle] = {}
        self._active_preset = "default"
        self._zones: dict[str, list[tuple[float, float]]] = {}
        self._refresh_zones()

    def set_preset(self, preset_id: str) -> None:
        if preset_id == self._active_preset:
            self._refresh_zones()
            return
        self._active_preset = preset_id
        self._tracks.clear()
        self._vehicles.clear()
        self._refresh_zones()

    def _refresh_zones(self) -> None:
        self._zones = get_store().for_camera_preset(self.camera_id, self._active_preset)

    def update(
        self,
        *,
        detections,
        frame_shape,
        camera_id: int,
        frame=None,
        vehicle_detections: Optional[list] = None,
        preset_id: Optional[str] = None,
        zones: Optional[dict[str, list[tuple[float, float]]]] = None,
    ) -> list[BehaviourAlert]:
        del frame_shape, frame  # reserved for future use
        del camera_id  # we already hold it on self
        if preset_id is not None and preset_id != self._active_preset:
            self.set_preset(preset_id)
        if zones is not None:
            # Caller provided world-registered zones for this frame; use them
            # verbatim instead of the raw store lookup.
            self._zones = zones
        else:
            self._refresh_zones()

        now = time.monotonic()
        alerts: list[BehaviourAlert] = []

        seen_track_ids: set[int] = set()
        for det in detections or []:
            track_id = det.track_id
            if track_id is None:
                continue
            seen_track_ids.add(track_id)
            person = self._tracks.setdefault(track_id, TrackedPerson(track_id=track_id))
            person.bbox = det.bbox
            person.last_seen = now
            self._classify_zones(person)
            alerts.extend(self._fire_zone_alerts(person))
            alerts.extend(self._fire_loitering(person, now))

        # Drop tracks that we haven't seen for ~3 s
        for tid in [tid for tid, p in self._tracks.items() if now - p.last_seen > 3.0]:
            self._tracks.pop(tid, None)

        # Vehicles
        for det in vehicle_detections or []:
            tid = det.track_id
            if tid is None:
                continue
            vehicle = self._vehicles.setdefault(tid, TrackedVehicle(track_id=tid))
            vehicle.bbox = det.bbox
            vehicle.last_seen = now
            vehicle.in_intrusion_zone = self._point_in_zone(self._centroid(det.bbox), "intrusion")

        for tid in [tid for tid, v in self._vehicles.items() if now - v.last_seen > 3.0]:
            self._vehicles.pop(tid, None)

        return alerts

    # ─── helpers ───────────────────────────────────────────────────

    def _classify_zones(self, person: TrackedPerson) -> None:
        foot = self._foot_point(person.bbox)
        person.in_intrusion_zone = self._point_in_zone(foot, "intrusion")
        person.in_climbing_zone = self._point_in_zone(foot, "climbing")
        person.in_gate_zone = self._point_in_zone(foot, "gate")
        person.in_generator_zone = self._point_in_zone(foot, "generator")

    def _fire_zone_alerts(self, person: TrackedPerson) -> list[BehaviourAlert]:
        """Once per zone entry. Repeats only after the track leaves and returns."""
        zone_state = [
            ("climbing", person.in_climbing_zone, {}),
            ("gate_breach", person.in_gate_zone, {}),
            ("intrusion", person.in_intrusion_zone and not person.in_gate_zone, {}),
            ("generator_zone_intrusion", person.in_generator_zone, {}),
        ]
        out: list[BehaviourAlert] = []
        now = time.monotonic()
        for alert_type, active, details in zone_state:
            if active:
                if alert_type not in person.zone_alerts_fired:
                    person.zone_alerts_fired.add(alert_type)
                    person.zone_entry_at[alert_type] = now
                    out.append(self._make(alert_type, person, details))
            else:
                person.zone_alerts_fired.discard(alert_type)
                person.zone_entry_at.pop(alert_type, None)
        return out

    def _fire_loitering(self, person: TrackedPerson, now: float) -> list[BehaviourAlert]:
        if not (person.in_intrusion_zone or person.in_gate_zone):
            person.loitering_started_at = None
            return []
        if person.loitering_started_at is None:
            person.loitering_started_at = now
            return []
        threshold = (
            settings.behaviour.loitering_gate_seconds
            if person.in_gate_zone
            else settings.behaviour.loitering_seconds
        )
        if now - person.loitering_started_at >= threshold and not person.loitering_alerted:
            person.loitering_alerted = True
            return [self._make("loitering", person, {"seconds": int(now - person.loitering_started_at)})]
        return []

    def _make(self, alert_type: str, person: TrackedPerson, details: dict) -> BehaviourAlert:
        return BehaviourAlert(
            alert_type=alert_type,
            camera_id=self.camera_id,
            track_id=person.track_id,
            details=details,
        )

    def _point_in_zone(self, point, zone_name: str) -> bool:
        polygon = self._zones.get(zone_name)
        if not polygon:
            return False
        return point_in_polygon(point, polygon)

    @staticmethod
    def _foot_point(bbox) -> tuple[float, float]:
        if not bbox:
            return (0.0, 0.0)
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2.0, y2)

    @staticmethod
    def _centroid(bbox) -> tuple[float, float]:
        if not bbox:
            return (0.0, 0.0)
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
