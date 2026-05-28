"""Per-camera operator settings that don't fit anywhere else.

The first (and currently only) setting is `base_preset_name`: which
PTZ preset the system should auto-pan a camera to when the RTSP
stream comes back after a disrupt. Without it, a power blip or
reboot leaves the camera parked wherever the firmware decides
(often at the manufacturer's default home), the preset_state
tracker no longer matches physical reality, and zones either fire
on the wrong patch of scene or stay dormant until the operator
clicks a preset by hand.

JSON-backed so the value survives container restarts. Sized for
tens of cameras — full rewrite on every change is fine.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CameraSettings:
    base_preset_name: Optional[str] = None


class CameraSettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._by_camera: dict[int, CameraSettings] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("camera settings at %s unreadable: %s", self._path, exc)
            return
        with self._lock:
            for cam_key, block in (raw or {}).items():
                try:
                    cam_id = int(cam_key)
                except (ValueError, TypeError):
                    continue
                if not isinstance(block, dict):
                    continue
                bp = block.get("base_preset_name")
                self._by_camera[cam_id] = CameraSettings(
                    base_preset_name=str(bp).strip() if bp else None,
                )

    def _save_locked(self) -> None:
        payload = {str(cam): asdict(s) for cam, s in self._by_camera.items()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, camera_id: int) -> CameraSettings:
        with self._lock:
            return self._by_camera.get(int(camera_id)) or CameraSettings()

    def all(self) -> dict[int, CameraSettings]:
        with self._lock:
            return dict(self._by_camera)

    def set_base_preset(self, camera_id: int, preset_name: Optional[str]) -> None:
        name = (preset_name or "").strip() or None
        with self._lock:
            current = self._by_camera.get(int(camera_id)) or CameraSettings()
            current.base_preset_name = name
            self._by_camera[int(camera_id)] = current
            self._save_locked()
        logger.info("cam%d base preset set to %r", camera_id, name)
