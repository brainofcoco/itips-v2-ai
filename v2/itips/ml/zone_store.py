"""Per-camera zone polygons for the behavioral fallback.

Two shapes: `region` (closed polygon, drives intrusion/loitering) and
`line` (polyline, drives line-crossing). Coords are normalised [0,1]
so zones survive snapshot resolution changes.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


ZONE_TYPE_REGION = "region"
ZONE_TYPE_LINE = "line"
_VALID_TYPES = {ZONE_TYPE_REGION, ZONE_TYPE_LINE}


@dataclass
class Zone:
    zone_id: str
    zone_type: str                              # region | line
    points: list[tuple[float, float]]           # normalised [0,1]
    name: str = ""
    direction: str = "Any"                      # Any | LeftToRight | RightToLeft
    # Camera-side PTZ preset this zone is bound to. When set, the
    # behavior engine only evaluates the zone (and the Live overlay only
    # draws it) while the camera is currently at this preset — drawn
    # coordinates are only meaningful for that physical orientation.
    # `None` means "always active", which suits fixed-view cameras and
    # is the back-compat default for zones created before this field
    # existed.
    preset_name: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.zone_type not in _VALID_TYPES:
            raise ValueError(f"zone_type must be one of {_VALID_TYPES}, got {self.zone_type!r}")
        if self.zone_type == ZONE_TYPE_REGION and len(self.points) < 3:
            raise ValueError(f"region zone needs ≥3 points, got {len(self.points)}")
        if self.zone_type == ZONE_TYPE_LINE and len(self.points) < 2:
            raise ValueError(f"line zone needs ≥2 points, got {len(self.points)}")
        self.points = [(float(p[0]), float(p[1])) for p in self.points]


class ZoneStore:
    """Thread-safe JSON-backed per-camera zone registry."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._by_camera: dict[int, list[Zone]] = {}
        self._load()

    # ─── persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("zone store at %s unreadable: %s", self._path, exc)
            return
        with self._lock:
            self._by_camera = {}
            for cam_key, cam_block in (raw or {}).items():
                try:
                    cam_id = int(cam_key)
                except (ValueError, TypeError):
                    continue
                zones_raw = cam_block.get("zones", []) if isinstance(cam_block, dict) else []
                zones: list[Zone] = []
                for z in zones_raw:
                    try:
                        preset_raw = z.get("preset_name")
                        preset_name = str(preset_raw).strip() if preset_raw else None
                        zones.append(Zone(
                            zone_id=str(z["zone_id"]),
                            zone_type=str(z["zone_type"]),
                            points=[tuple(p) for p in z.get("points", [])],
                            name=str(z.get("name", "")),
                            direction=str(z.get("direction", "Any")),
                            preset_name=preset_name or None,
                            metadata=dict(z.get("metadata") or {}),
                        ))
                    except (KeyError, ValueError) as exc:
                        logger.warning("skipping invalid zone for cam %d: %s",
                                       cam_id, exc)
                self._by_camera[cam_id] = zones

    def _save_locked(self) -> None:
        """Caller holds `_lock`. Atomic write via tmp + rename."""
        payload = {
            str(cam): {"zones": [asdict(z) for z in zones]}
            for cam, zones in self._by_camera.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ─── read ─────────────────────────────────────────────────────────

    def for_camera(self, camera_id: int) -> list[Zone]:
        with self._lock:
            return list(self._by_camera.get(camera_id, []))

    def all(self) -> dict[int, list[Zone]]:
        with self._lock:
            return {cam: list(zones) for cam, zones in self._by_camera.items()}

    # ─── write ────────────────────────────────────────────────────────

    def upsert_zone(self, camera_id: int, zone: Zone) -> None:
        with self._lock:
            zones = self._by_camera.setdefault(camera_id, [])
            for i, existing in enumerate(zones):
                if existing.zone_id == zone.zone_id:
                    zones[i] = zone
                    break
            else:
                zones.append(zone)
            self._save_locked()

    def remove_zone(self, camera_id: int, zone_id: str) -> bool:
        with self._lock:
            zones = self._by_camera.get(camera_id, [])
            for i, z in enumerate(zones):
                if z.zone_id == zone_id:
                    del zones[i]
                    self._save_locked()
                    return True
            return False

    def replace_for_camera(self, camera_id: int, zones: list[Zone]) -> None:
        with self._lock:
            self._by_camera[camera_id] = list(zones)
            self._save_locked()


# ─── geometry helpers ────────────────────────────────────────────────


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting PIP test."""
    if len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def segments_intersect(
    a1: tuple[float, float], a2: tuple[float, float],
    b1: tuple[float, float], b2: tuple[float, float],
) -> bool:
    """CCW segment-intersection test; excludes colinear-overlap (unwanted
    for line-crossing semantics)."""
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])

    return ccw(a1, b1, b2) != ccw(a2, b1, b2) and ccw(a1, a2, b1) != ccw(a1, a2, b2)
