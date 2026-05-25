"""Per-camera active-preset state, with sim-PTZ crop params.

A *preset* is a named viewpoint a PTZ camera can return to. Each camera
has a set of presets defined in `config/presets.example.json`; one of
them is active at any moment. Both the behaviour analyser (zone lookup)
and the virtual PTZ (digital crop) consult the active preset.

For a non-PTZ camera, the preset set collapses to a single implicit
'default' preset with pan/tilt/zoom = (0, 0, 1.0), which the virtual
PTZ treats as a no-op.

The registry is a small thread-safe mailbox — it does not move real
PTZ hardware. The PTZ controller is responsible for that and writes
back here once a move has been applied.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PresetParams:
    """Sim-PTZ crop parameters, in normalised units.

    pan, tilt  ∈ [-1.0, 1.0]   horizontal/vertical offset of the crop centre
    zoom       ≥ 1.0           1.0 = full frame, 2.0 = quarter-area crop
    """

    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 1.0


_DEFAULT_PRESET = "default"
_DEFAULT_PARAMS = PresetParams()


class PresetRegistry:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._active: dict[int, str] = {}                # camera_id → preset_id
        self._defs: dict[int, dict[str, PresetParams]] = {}
        if config_path and Path(config_path).exists():
            self.load(Path(config_path))

    # ─── config ────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        defs: dict[int, dict[str, PresetParams]] = {}
        for cam_id, presets in body.items():
            if not isinstance(presets, dict):
                continue   # allow free-form keys like "_comment"
            try:
                key = int(cam_id)
            except (TypeError, ValueError):
                continue
            cam_defs: dict[str, PresetParams] = {}
            for preset_id, params in presets.items():
                if not isinstance(params, dict):
                    continue
                cam_defs[preset_id] = PresetParams(
                    pan=float(params.get("pan", 0.0)),
                    tilt=float(params.get("tilt", 0.0)),
                    zoom=float(params.get("zoom", 1.0)),
                )
            defs[key] = cam_defs
        with self._lock:
            self._defs = defs
            for cam_id, cam_defs in defs.items():
                if cam_id not in self._active and cam_defs:
                    self._active[cam_id] = next(iter(cam_defs))
        logger.info("Loaded presets for %d cameras from %s",
                    len(defs), path)

    # ─── active preset ─────────────────────────────────────────────

    def active(self, camera_id: int) -> str:
        with self._lock:
            return self._active.get(camera_id, _DEFAULT_PRESET)

    def set_active(self, camera_id: int, preset_id: str) -> bool:
        with self._lock:
            cam_defs = self._defs.get(camera_id, {})
            if preset_id != _DEFAULT_PRESET and preset_id not in cam_defs:
                return False
            self._active[camera_id] = preset_id
        logger.info("Camera %d → preset '%s'", camera_id, preset_id)
        return True

    # ─── definitions ───────────────────────────────────────────────

    def params(self, camera_id: int, preset_id: Optional[str] = None) -> PresetParams:
        with self._lock:
            preset_id = preset_id or self._active.get(camera_id, _DEFAULT_PRESET)
            return self._defs.get(camera_id, {}).get(preset_id, _DEFAULT_PARAMS)

    def list_for(self, camera_id: int) -> list[str]:
        with self._lock:
            return list(self._defs.get(camera_id, {}).keys()) or [_DEFAULT_PRESET]

    def has_definitions(self, camera_id: int) -> bool:
        with self._lock:
            return bool(self._defs.get(camera_id))
