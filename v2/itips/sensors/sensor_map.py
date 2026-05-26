"""Persistent zone_id → camera+preset registry for the sensor pipeline.

When the AX PRO hub fires an alarm on zone N, the dispatcher needs to
know which camera to pan and which preset to send it to. The PRD
(§2.5) makes this a configuration concern — adding a sensor is meant
to be a config push, never a code change. This module owns that map.

Schema, JSON on disk:

    {
      "1": {
        "camera_id": 4,
        "preset_name": "Gate View",
        "sensor_type": "doorContact",      # advisory; informs the
                                           # dispatcher's debounce
        "description": "Front gate magnetic contact"
      },
      "3": {"camera_id": 4, "preset_name": "Generator", ...}
    }

Keys are stringified zone IDs (JSON's the constraint) but the public
API uses `int` everywhere — the str↔int conversion lives inside this
module so consumers never see it. Pattern mirrors `zone_store.py` and
`ml_overrides.json`: atomic write, graceful read of corrupt files,
single-writer thread safety.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SensorMapping:
    """One zone → camera+preset assignment."""

    zone_id: int
    camera_id: int
    preset_name: str
    sensor_type: str = ""           # PIR / vibration / doorContact / pircam / …
    description: str = ""           # operator-facing label
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.preset_name:
            raise ValueError("preset_name is required")
        if int(self.camera_id) <= 0:
            raise ValueError(f"camera_id must be positive, got {self.camera_id}")
        self.zone_id = int(self.zone_id)
        self.camera_id = int(self.camera_id)


class SensorMap:
    """Thread-safe JSON-backed sensor-to-camera mapping store."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._by_zone: dict[int, SensorMapping] = {}
        self._load()

    # ─── persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("sensor map at %s unreadable: %s", self._path, exc)
            return
        with self._lock:
            self._by_zone = {}
            for zone_key, row in (raw or {}).items():
                if not isinstance(row, dict):
                    continue
                try:
                    mapping = SensorMapping(
                        zone_id=int(zone_key),
                        camera_id=int(row["camera_id"]),
                        preset_name=str(row["preset_name"]),
                        sensor_type=str(row.get("sensor_type", "")),
                        description=str(row.get("description", "")),
                        metadata=dict(row.get("metadata") or {}),
                    )
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("skipping invalid sensor mapping %s: %s",
                                   zone_key, exc)
                    continue
                self._by_zone[mapping.zone_id] = mapping
        if self._by_zone:
            logger.info("loaded %d sensor mapping(s) from %s",
                        len(self._by_zone), self._path)

    def _save_locked(self) -> None:
        payload = {
            str(zone_id): {
                k: v for k, v in asdict(m).items() if k != "zone_id"
            }
            for zone_id, m in self._by_zone.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ─── read ─────────────────────────────────────────────────────────

    def get(self, zone_id: int) -> Optional[SensorMapping]:
        with self._lock:
            return self._by_zone.get(int(zone_id))

    def all(self) -> list[SensorMapping]:
        with self._lock:
            return sorted(self._by_zone.values(), key=lambda m: m.zone_id)

    def __contains__(self, zone_id: int) -> bool:
        with self._lock:
            return int(zone_id) in self._by_zone

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_zone)

    # ─── write ────────────────────────────────────────────────────────

    def upsert(self, mapping: SensorMapping) -> None:
        with self._lock:
            self._by_zone[mapping.zone_id] = mapping
            self._save_locked()

    def remove(self, zone_id: int) -> bool:
        with self._lock:
            if int(zone_id) not in self._by_zone:
                return False
            del self._by_zone[int(zone_id)]
            self._save_locked()
            return True
