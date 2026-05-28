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
        # camera_id -> unix_ts before which auto-restore is suppressed.
        # Set by operator-initiated clears (manual jog) so the recovery
        # watcher doesn't yank the camera back to base while the operator
        # is looking around.
        self._manual_until: dict[int, float] = {}

    def record_goto(self, camera_id: int, preset_name: str) -> None:
        """Note that the camera is now (or about to be) at `preset_name`.

        Called by every path that pans the camera to a preset. Idempotent
        — same preset name back-to-back just refreshes the timestamp.
        Always clears any pending manual-override window (a successful
        goto is a stronger signal than the operator's last jog).
        """
        name = (preset_name or "").strip()
        if not name:
            return
        with self._lock:
            self._state[int(camera_id)] = (name, time.time())
            self._manual_until.pop(int(camera_id), None)
        logger.debug("preset_state: cam%d → '%s'", camera_id, name)

    def clear(self, camera_id: int, *, manual_grace_s: float = 0.0) -> None:
        """Forget the camera's preset.

        `manual_grace_s` suppresses the recovery watcher for that many
        seconds — used when the *operator* moves the camera (manual
        jog) so we don't immediately yank it back to base while they're
        still looking around. Default 0 is right for system-initiated
        clears (stream disrupt) — restore as soon as possible.
        """
        cid = int(camera_id)
        with self._lock:
            self._state.pop(cid, None)
            if manual_grace_s > 0:
                self._manual_until[cid] = time.time() + float(manual_grace_s)
            else:
                # An explicit system clear cancels any prior manual hold —
                # otherwise a jog 4 minutes ago would keep blocking
                # recovery from a real disrupt happening now.
                self._manual_until.pop(cid, None)

    def auto_restore_allowed(self, camera_id: int) -> bool:
        with self._lock:
            until = self._manual_until.get(int(camera_id), 0.0)
            return time.time() >= until

    def manual_hold_remaining_s(self, camera_id: int) -> float:
        with self._lock:
            until = self._manual_until.get(int(camera_id), 0.0)
            return max(0.0, until - time.time())

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
