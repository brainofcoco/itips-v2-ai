"""Tracked-entity dataclasses.

ByteTrack provides the IDs (from `YOLOEngine.detect`), so this module
only models the *behavioural* state we accrue per track: which zones
they've entered, when, how long they've been loitering, etc.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TrackedPerson:
    track_id: int
    bbox: tuple[float, float, float, float] | None = None
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    in_intrusion_zone: bool = False
    in_climbing_zone: bool = False
    in_gate_zone: bool = False
    in_generator_zone: bool = False
    loitering_alerted: bool = False
    loitering_started_at: float | None = None
    # Zone names we've already fired an alert for during the current
    # zone occupancy. Cleared when the person leaves that zone, so
    # re-entry fires a fresh alert.
    zone_alerts_fired: set[str] = field(default_factory=set)
    # Monotonic time the person first entered the active zone — used by
    # the two-stage incident engine to test dwell.
    zone_entry_at: dict[str, float] = field(default_factory=dict)


@dataclass
class TrackedVehicle:
    track_id: int
    bbox: tuple[float, float, float, float] | None = None
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    in_intrusion_zone: bool = False
