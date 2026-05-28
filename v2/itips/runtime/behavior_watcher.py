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
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _encode_capture_jpeg(frame, max_width: int = 1280) -> Optional[bytes]:
    """Downscale a (possibly 4K) BGR frame to `max_width` and JPEG-encode
    it for human review. Full-res captures are slow to load and needlessly
    large; 1280px is plenty to eyeball a subject in a zone."""
    try:
        import cv2
        h, w = frame.shape[:2]
        if w > max_width:
            scale = max_width / float(w)
            frame = cv2.resize(frame, (max_width, int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes() if ok else None
    except Exception:
        return None


class BehaviorWatcher(threading.Thread):
    def __init__(
        self,
        *,
        frame_bus,
        behavior_engine,
        dahua_manager,
        activity_tap,
        threat_evaluator=None,
        target_fps: float = 2.0,
        repeat_cooldown_s: float = 8.0,
    ) -> None:
        super().__init__(name="behavior-watcher", daemon=True)
        self._frame_bus = frame_bus
        self._engine = behavior_engine
        self._dahua = dahua_manager
        self._activity = activity_tap
        # When set, each detection opens a ThreatEvaluator window so the
        # scene is sampled for an authorised-worker face and a verdict is
        # produced (which lands in the Investigations feed). Without this
        # the watcher only paints the Live badge and never escalates.
        self._threat_evaluator = threat_evaluator
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
        # Engine may still be warming up its model. If a transient import
        # failure killed the initial warmup, kick another attempt rather
        # than silently skipping forever (which is what left the Live
        # intrusion warnings dead after a flaky boot).
        if not self._engine.is_ready():
            try:
                self._engine.warmup_async()
            except Exception:
                pass
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
                self._trigger_evaluation(camera_id, alert, snap.raw)

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

    def _trigger_evaluation(self, camera_id: int, alert: Any, frame: Any) -> None:
        """Open (or extend) a ThreatEvaluator window for this detection.

        `trigger` is idempotent — calling it every frame while a subject
        is in the zone rolls into one window and one verdict, not N. We
        also hand over an *annotated* evidence frame (the actual frame the
        detection fired on, with the zone outline + detection box drawn)
        so the Investigations page shows the subject in the zone — the
        evaluator's own later snapshots often catch an empty scene once a
        fast subject has moved on."""
        if self._threat_evaluator is None:
            return
        kind = {
            "intrusion": "behavior:region_intrusion",
            "line_crossing": "behavior:line_cross",
            "loitering": "behavior:loiter",
        }.get(alert.alert_type, f"behavior:{alert.alert_type}")
        evidence = self._annotate_evidence(camera_id, alert, frame)
        try:
            self._threat_evaluator.trigger(
                camera_id=camera_id,
                trigger_kind=kind,
                initial_frame=frame,
                evidence_jpeg=evidence,
                details={
                    "zone_id": getattr(alert, "zone_id", None),
                    "zone_name": getattr(alert, "zone_name", ""),
                    "class_name": getattr(alert, "class_name", ""),
                },
            )
        except Exception:
            logger.exception(
                "BehaviorWatcher: cam%d threat-eval trigger failed", camera_id,
            )

    def _annotate_evidence(self, camera_id: int, alert: Any, frame: Any):
        """Return a downscaled JPEG of `frame` with the active zone
        outlines and the detection bbox drawn on it, or None on failure."""
        try:
            import cv2
            import numpy as np
            img = frame.copy()
            h, w = img.shape[:2]
            # Active zone outlines (green), with the triggering zone bright.
            try:
                zones = self._engine.active_zones(camera_id)
            except Exception:
                zones = []
            trig_zone = str(getattr(alert, "zone_id", "") or "")
            for z in zones:
                pts = np.array(
                    [[int(px * w), int(py * h)] for px, py in z.points],
                    dtype=np.int32,
                )
                if len(pts) < 2:
                    continue
                hot = (str(z.zone_id) == trig_zone)
                color = (0, 0, 255) if hot else (0, 200, 0)  # BGR: red hot, green others
                closed = z.zone_type == "region"
                cv2.polylines(img, [pts], closed, color, max(2, w // 640))
            # Detection box (yellow) + label.
            bbox = getattr(alert, "bbox", None)
            if bbox and len(bbox) == 4:
                x1, y1, x2, y2 = (int(v) for v in bbox)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), max(2, w // 640))
                label = f"{getattr(alert, 'class_name', '')} {alert.alert_type}".strip()
                cv2.putText(img, label, (x1, max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, max(0.6, w / 2400),
                            (0, 255, 255), 2, cv2.LINE_AA)
            return _encode_capture_jpeg(img)
        except Exception:
            logger.exception("BehaviorWatcher: cam%d evidence annotation failed", camera_id)
            return None

    def _maybe_publish(self, camera_id: int, alert: Any) -> None:
        key = (camera_id, str(getattr(alert, "zone_id", "")), alert.alert_type)
        now = time.monotonic()
        last = self._last_published.get(key, 0.0)
        if now - last < self._repeat_cooldown_s:
            return
        self._last_published[key] = now
        detail = dict(getattr(alert, "details", {}) or {})
        detail.setdefault("class_name", getattr(alert, "class_name", ""))
        # If this camera is held off after an authorized worker, the activity
        # is that same worker still moving around — surface it as "authorized"
        # so the Live tile shows a green badge, not an intrusion alarm.
        person = self._holdoff_person(camera_id)
        if person is not None:
            detail["person_name"] = person
            status = "authorized"
        else:
            status = "evaluating"
        self._activity.publish(
            camera_id=camera_id,
            kind=alert.alert_type,
            zone_id=getattr(alert, "zone_id", None),
            zone_name=getattr(alert, "zone_name", ""),
            detail=detail,
            status=status,
            source="behavior",
        )
        logger.info(
            "BehaviorWatcher: cam%d %s in zone '%s' — activity published (%s)",
            camera_id, alert.alert_type,
            getattr(alert, "zone_name", "") or getattr(alert, "zone_id", "?"),
            status,
        )

    def _holdoff_person(self, camera_id: int) -> Optional[str]:
        """Authorized worker name if the evaluator is holding this camera
        off, else None. Defensive — evaluator may be absent or older."""
        ev = self._threat_evaluator
        if ev is None or not hasattr(ev, "holdoff_person"):
            return None
        try:
            return ev.holdoff_person(camera_id)
        except Exception:
            return None
