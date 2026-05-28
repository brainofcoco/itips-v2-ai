"""Background loop that re-establishes a known PTZ orientation.

The RTSP grabber's disrupt/reconnect listeners (`_wire_camera_recovery`
in app.py) only fire when a *full* open→close→re-open cycle happens.
Real-world camera flakiness — a brief frame stutter, a network blip
short enough that cv2 keeps the capture handle alive, a container
restart, even a missed reconnect event — leaves the system in a
state where `preset_state` is empty but no recovery callback ever
runs. Zones for that camera stay dormant indefinitely and the
operator has to click a preset by hand.

This watcher closes that gap: every `interval_s` it checks each
camera and, if `preset_state` has no entry, attempts to pan the
camera to its operator-configured base preset. Per-camera
exponential backoff keeps it from hammering a camera that's
genuinely down.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class CameraRecoveryWatcher(threading.Thread):

    def __init__(
        self,
        *,
        dahua_manager,
        preset_state,
        camera_settings,
        interval_s: float = 15.0,
        max_backoff_s: float = 300.0,
    ) -> None:
        super().__init__(name="camera-recovery", daemon=True)
        self._dahua = dahua_manager
        self._preset_state = preset_state
        self._settings = camera_settings
        self._interval_s = float(interval_s)
        self._max_backoff_s = float(max_backoff_s)
        self._stop = threading.Event()
        # Per-camera last-attempt timestamp and current backoff window —
        # so a camera that's persistently down doesn't get a goto every
        # 15s forever.
        self._last_attempt_ts: dict[int, float] = {}
        self._current_backoff_s: dict[int, float] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        logger.info(
            "CameraRecoveryWatcher starting — interval=%.0fs, max_backoff=%.0fs",
            self._interval_s, self._max_backoff_s,
        )
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("recovery watcher tick crashed")
            if self._stop.wait(self._interval_s):
                break
        logger.info("CameraRecoveryWatcher stopped")

    def _tick(self) -> None:
        for camera_id in self._dahua.camera_ids():
            self._maybe_restore(camera_id)

    def _maybe_restore(self, camera_id: int) -> None:
        # Already know where the camera is — nothing to do.
        if self._preset_state.current(camera_id) is not None:
            # Clear our backoff because a successful state means whatever
            # was wrong has resolved (likely operator action or sensor
            # binding pan).
            self._current_backoff_s.pop(camera_id, None)
            self._last_attempt_ts.pop(camera_id, None)
            return
        # Operator is actively driving — leave the camera alone.
        if not self._preset_state.auto_restore_allowed(camera_id):
            return
        base = self._settings.get(camera_id).base_preset_name
        if not base:
            return    # nothing configured; respect operator's choice
        # Respect per-camera backoff between attempts.
        now = time.time()
        backoff = self._current_backoff_s.get(camera_id, 0.0)
        last = self._last_attempt_ts.get(camera_id, 0.0)
        if now - last < backoff:
            return
        client = self._dahua.get(camera_id)
        if client is None:
            return
        self._last_attempt_ts[camera_id] = now
        try:
            ok = client.ptz.goto_preset_by_name(base)
        except Exception:
            logger.exception(
                "recovery watcher: cam%d goto %r crashed", camera_id, base,
            )
            ok = False
        if ok:
            self._preset_state.record_goto(camera_id, base)
            self._current_backoff_s.pop(camera_id, None)
            self._last_attempt_ts.pop(camera_id, None)
            logger.info(
                "recovery watcher: cam%d restored to base preset %r",
                camera_id, base,
            )
            return
        # Failure — grow the backoff toward the cap so we don't spam
        # a camera that's still booting / unreachable.
        next_backoff = min(
            max(self._interval_s, backoff * 2 if backoff else self._interval_s),
            self._max_backoff_s,
        )
        self._current_backoff_s[camera_id] = next_backoff
        logger.warning(
            "recovery watcher: cam%d goto %r failed; next attempt in ~%.0fs",
            camera_id, base, next_backoff,
        )
