"""Per-camera "what preset is the camera at right now?" tracker.

Why this exists: zones are drawn in normalised frame coordinates and
only make sense when the camera is at the same physical orientation
they were drawn under. After a PTZ pan to a different preset, the
zones still draw over *something*, but it's the wrong thing. To stop
the false-alarm churn we gate zone evaluation and the Live overlay on
the camera being at the zone's bound preset.

We can't reliably ask the camera "what preset are you at?" — Dahua
firmwares vary, and a manual pan via the camera's own web UI leaves
no trail. So we track it from *our* side: every time something in
ITIPS commands a goto-preset (PTZ panel, sensor binding fire,
calibration), we record it here. When ITIPS hasn't issued a goto yet
(fresh boot) or someone bypasses us, we report `None` and the rest of
the stack hides zones rather than firing on the wrong view.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class PresetStateTracker:
    """Thread-safe in-memory dict of `camera_id -> last commanded preset name`.

    In-memory by design: the only thing it loses on restart is the
    knowledge of which preset each camera was at, which is the safest
    starting state anyway (we'd rather hide zones than evaluate them
    against a stale assumption).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # camera_id -> (preset_name, recorded_ts)
        self._state: dict[int, tuple[str, float]] = {}

    def record_goto(self, camera_id: int, preset_name: str) -> None:
        """Note that the camera is now (or about to be) at `preset_name`.

        Called by every path that pans the camera to a preset. Idempotent
        — same preset name back-to-back just refreshes the timestamp.
        """
        name = (preset_name or "").strip()
        if not name:
            return
        with self._lock:
            self._state[int(camera_id)] = (name, time.time())
        logger.debug("preset_state: cam%d → '%s'", camera_id, name)

    def clear(self, camera_id: int) -> None:
        """Forget the camera's preset (e.g. on a manual jog where we
        no longer know where it's pointing)."""
        with self._lock:
            self._state.pop(int(camera_id), None)

    def current(self, camera_id: int) -> Optional[str]:
        with self._lock:
            entry = self._state.get(int(camera_id))
            return entry[0] if entry else None

    def current_with_age(self, camera_id: int) -> tuple[Optional[str], Optional[float]]:
        with self._lock:
            entry = self._state.get(int(camera_id))
            if not entry:
                return None, None
            name, ts = entry
            return name, time.time() - ts

    def all(self) -> dict[int, Optional[str]]:
        with self._lock:
            return {cam: name for cam, (name, _) in self._state.items()}
