"""Continuous zone evaluation against the live RTSP frames.

Until now the behavior engine only ran as a *fallback*, on a single
frame, when a Dahua VideoMotion event happened to fire. That meant
ITIPS wasn't actually watching the configured zones — it was leaning on
the camera's onboard IVS and only synthesising alerts reactively.

This watcher closes that gap: a low-rate loop pulls the newest frame
each camera published to the FrameBus and runs `behavior_engine.analyse`
against the zones that are active for the camera's current preset (the
engine already gates on `PresetStateTracker`). Detections are pushed to
the `ActivityTap` so the Live page can show "line crossed · evaluating"
the instant it happens — before any OpenAI validation or incident.

It runs at a deliberately low frame rate (a couple of fps) because YOLO
on CPU is expensive; the Jetson GPU can take it higher. Cameras with no
active zones cost almost nothing — `analyse` short-circuits before the
detector runs.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class BehaviorWatcher(threading.Thread):
    def __init__(
        self,
        *,
        frame_bus,
        behavior_engine,
        dahua_manager,
        activity_tap,
        target_fps: float = 2.0,
        repeat_cooldown_s: float = 8.0,
    ) -> None:
        super().__init__(name="behavior-watcher", daemon=True)
        self._frame_bus = frame_bus
        self._engine = behavior_engine
        self._dahua = dahua_manager
        self._activity = activity_tap
        self._target_fps = max(0.5, float(target_fps))
        self._repeat_cooldown_s = float(repeat_cooldown_s)
        self._stop = threading.Event()
        # Last time we published a given (camera, zone, kind) so a person
        # standing inside a region doesn't spam one event per frame.
        self._last_published: dict[tuple[int, str, str], float] = {}
        # Rate-limit for the detection heartbeat log, per camera.
        self._last_detlog: dict[int, float] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if self._engine is None:
            logger.info("BehaviorWatcher: no behavior engine — watcher idle")
            return
        period = 1.0 / self._target_fps
        logger.info(
            "BehaviorWatcher starting — target ~%.1f fps/camera", self._target_fps,
        )
        while not self._stop.is_set():
            start = time.monotonic()
            try:
                self._tick()
            except Exception:
                logger.exception("BehaviorWatcher tick crashed")
            elapsed = time.monotonic() - start
            # Sleep the remainder of the period; never busy-spin.
            if self._stop.wait(max(0.05, period - elapsed)):
                break
        logger.info("BehaviorWatcher stopped")

    def _tick(self) -> None:
        # Engine may still be warming up its model; skip cheaply.
        if not self._engine.is_ready():
            return
        for camera_id in self._dahua.camera_ids():
            snap = self._frame_bus.latest(camera_id)
            if snap is None or snap.raw is None:
                continue
            try:
                analysis = self._engine.analyse_detailed(camera_id, snap.raw)
            except Exception:
                logger.exception("BehaviorWatcher: cam%d analyse crashed", camera_id)
                continue
            self._log_detections(camera_id, analysis)
            for alert in analysis.alerts:
                self._maybe_publish(camera_id, alert)

    def _log_detections(self, camera_id: int, analysis) -> None:
        """Heartbeat showing what YOLO + tracker saw, so a 'crossing
        didn't fire' can be diagnosed: did we even detect the object?
        Rate-limited to once / ~2s per camera and only when there's
        something to report."""
        dets = getattr(analysis, "detections", []) or []
        tracks = getattr(analysis, "tracks", []) or []
        if not dets and not analysis.alerts:
            return
        now = time.monotonic()
        last = self._last_detlog.get(camera_id, 0.0)
        if now - last < 2.0:
            return
        self._last_detlog[camera_id] = now
        classes = ",".join(sorted({getattr(d, "class_name", "?") for d in dets})) or "-"
        logger.info(
            "BehaviorWatcher: cam%d dets=%d (%s) tracks=%d alerts=%d",
            camera_id, len(dets), classes, len(tracks), len(analysis.alerts),
        )

    def _maybe_publish(self, camera_id: int, alert: Any) -> None:
        key = (camera_id, str(getattr(alert, "zone_id", "")), alert.alert_type)
        now = time.monotonic()
        last = self._last_published.get(key, 0.0)
        if now - last < self._repeat_cooldown_s:
            return
        self._last_published[key] = now
        detail = dict(getattr(alert, "details", {}) or {})
        detail.setdefault("class_name", getattr(alert, "class_name", ""))
        self._activity.publish(
            camera_id=camera_id,
            kind=alert.alert_type,
            zone_id=getattr(alert, "zone_id", None),
            zone_name=getattr(alert, "zone_name", ""),
            detail=detail,
            status="evaluating",
            source="behavior",
        )
        logger.info(
            "BehaviorWatcher: cam%d %s in zone '%s' — activity published",
            camera_id, alert.alert_type,
            getattr(alert, "zone_name", "") or getattr(alert, "zone_id", "?"),
        )
