"""Zone polygon store, keyed by (camera_id, preset_id).

Schema on disk:

    {
      "<camera_id>": {
        "<preset_id>": {
          "<zone_name>": [[x1, y1], [x2, y2], ...]
        }
      }
    }

A legacy flat shape ({"<camera_id>": {"<zone_name>": [...]}}) is read as
the implicit "default" preset so existing fixtures keep working.

Two sources of truth:
  - `config/zones.example.json` — checked in, immutable seed.
  - `var/zones.json`            — runtime overrides written by the dashboard.

The runtime file wins if it exists. Dashboard saves call `replace_preset`
which both updates memory and rewrites the runtime file.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PRESET = "default"

# camera_id → preset_id → zone_name → polygon
ZonesByPreset = dict[str, dict[str, list[tuple[float, float]]]]


def _looks_like_preset_schema(value: dict) -> bool:
    """Heuristic: is this value a {preset: {zone: poly}} mapping or the
    legacy {zone: poly} mapping? Treat any value whose first inner value
    is a list as legacy.
    """
    if not value:
        return False
    first = next(iter(value.values()))
    return isinstance(first, dict)


def _normalise(body: dict) -> dict[int, ZonesByPreset]:
    out: dict[int, ZonesByPreset] = {}
    for cam_id, value in body.items():
        try:
            key = int(cam_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if _looks_like_preset_schema(value):
            preset_map: ZonesByPreset = {}
            for preset_id, zones in value.items():
                preset_map[str(preset_id)] = {
                    str(name): [tuple(p) for p in polygon]
                    for name, polygon in zones.items()
                }
            out[key] = preset_map
        else:
            out[key] = {
                _DEFAULT_PRESET: {
                    str(name): [tuple(p) for p in polygon]
                    for name, polygon in value.items()
                }
            }
    return out


class ZoneStore:
    def __init__(
        self,
        seed_path: Optional[Path] = None,
        runtime_path: Optional[Path] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._runtime_path = Path(runtime_path) if runtime_path else None
        self._zones: dict[int, ZonesByPreset] = {}
        if seed_path and Path(seed_path).exists():
            self._zones = _normalise(json.loads(Path(seed_path).read_text(encoding="utf-8")))
        if self._runtime_path and self._runtime_path.exists():
            self._zones = _normalise(json.loads(self._runtime_path.read_text(encoding="utf-8")))

    def for_camera_preset(self, camera_id: int, preset_id: str) -> dict[str, list[tuple[float, float]]]:
        with self._lock:
            cam = self._zones.get(camera_id, {})
            return dict(cam.get(preset_id) or cam.get(_DEFAULT_PRESET) or {})

    def presets_for(self, camera_id: int) -> list[str]:
        with self._lock:
            return list((self._zones.get(camera_id) or {}).keys())

    def replace_preset(
        self,
        camera_id: int,
        preset_id: str,
        zones: dict[str, list[tuple[float, float]]],
    ) -> None:
        with self._lock:
            self._zones.setdefault(camera_id, {})[preset_id] = {
                str(name): [tuple(p) for p in polygon] for name, polygon in zones.items()
            }
            self._persist_locked()

    def _persist_locked(self) -> None:
        if not self._runtime_path:
            return
        self._runtime_path.parent.mkdir(parents=True, exist_ok=True)
        serialised = {
            str(cam_id): {
                preset_id: {name: [list(p) for p in polygon]
                            for name, polygon in zones.items()}
                for preset_id, zones in presets.items()
            }
            for cam_id, presets in self._zones.items()
        }
        self._runtime_path.write_text(
            json.dumps(serialised, indent=2, sort_keys=True), encoding="utf-8"
        )

    def snapshot(self) -> dict[int, ZonesByPreset]:
        with self._lock:
            return {
                cam: {preset: dict(zones) for preset, zones in presets.items()}
                for cam, presets in self._zones.items()
            }


_store: Optional[ZoneStore] = None
_store_lock = threading.Lock()


def init_store(seed_path: Optional[Path], runtime_path: Optional[Path]) -> ZoneStore:
    global _store
    with _store_lock:
        _store = ZoneStore(seed_path=seed_path, runtime_path=runtime_path)
    return _store


def get_store() -> ZoneStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ZoneStore(seed_path=Path("config/zones.example.json"))
    return _store
