"""Continuous low-FPS RTSP frame grabber.

One thread per camera. Opens an RTSP `VideoCapture`, drains the network
buffer every iteration so packets don't stack, and decodes one frame per
target-FPS interval to forward to registered consumers (typically a
single `IncidentRecorder` per camera).

Why this exists
---------------
In the Dahua-native event-driven architecture the Jetson does no
continuous video work — decoded frames only appear when an event JPEG
arrives in `eventManager.cgi`. That leaves `IncidentRecorder`'s pre-event
ring buffer empty when an incident opens and the post-event writer with
nothing to write between event bursts, so the saved `Video_cam{N}_*.mp4`
files come out blank or trivially short. This grabber re-introduces a
thin continuous feed dedicated to evidence recording.

CPU is kept low by:
  * using `cap.grab()` (no decode) every loop tick to drain RTSP frames,
  * calling `cap.retrieve()` (which decodes) only at the FPS cap,
  * preferring TCP transport via the FFmpeg backend so a flaky network
    doesn't produce torn frames.

Reconnects with exponential backoff on stream drop; exceptions inside
consumers are logged but never kill the thread.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class RtspFrameGrabber(threading.Thread):
    """Continuous RTSP reader. Fan-outs decoded frames at a fixed FPS cap."""

    def __init__(
        self,
        *,
        camera_id: int,
        rtsp_url: str,
        target_fps: float = 8.0,
        reconnect_backoff_s: float = 2.0,
        max_backoff_s: float = 30.0,
    ) -> None:
        super().__init__(name=f"rtsp-grab-{camera_id}", daemon=True)
        self.camera_id = camera_id
        self._rtsp_url = rtsp_url
        self._target_fps = max(1.0, float(target_fps))
        self._min_interval = 1.0 / self._target_fps
        self._reconnect_backoff_s = float(reconnect_backoff_s)
        self._max_backoff_s = float(max_backoff_s)
        self._stop = threading.Event()
        self._consumers: list[Callable[["np.ndarray"], None]] = []
        self._consumers_lock = threading.Lock()
        # Connect/disrupt observers — invoked on a *separate* worker
        # thread so a slow PTZ goto doesn't stall the grabber's RTSP
        # loop. Disrupt fires whenever a previously-open stream goes
        # away; reconnect fires after every subsequent re-open (not
        # the initial connect).
        self._disrupt_listeners: list[Callable[[int], None]] = []
        self._reconnect_listeners: list[Callable[[int], None]] = []

    # ─── public surface ───────────────────────────────────────────────

    def add_consumer(self, fn: Callable[["np.ndarray"], None]) -> None:
        """Subscribe a fan-out callable. Invoked on the grabber thread —
        must return quickly. `IncidentRecorder.feed` qualifies."""
        with self._consumers_lock:
            self._consumers.append(fn)

    def add_disrupt_listener(self, fn: Callable[[int], None]) -> None:
        """Called with `camera_id` whenever a connected stream drops."""
        self._disrupt_listeners.append(fn)

    def add_reconnect_listener(self, fn: Callable[[int], None]) -> None:
        """Called with `camera_id` after every *re*-open (post-drop)."""
        self._reconnect_listeners.append(fn)

    def stop(self) -> None:
        self._stop.set()

    def safe_label(self) -> str:
        """RTSP URL with credentials redacted for logs."""
        url = self._rtsp_url
        if "@" in url:
            scheme, _, rest = url.partition("://")
            _creds, _, hostpart = rest.partition("@")
            return f"{scheme}://***@{hostpart}"
        return url

    # ─── thread loop ──────────────────────────────────────────────────

    def run(self) -> None:
        try:
            import cv2
        except Exception:
            logger.exception("cam %d: cv2 unavailable — grabber idle",
                             self.camera_id)
            self._stop.wait()
            return

        backoff = self._reconnect_backoff_s
        # `has_been_open` distinguishes a true reconnect from the
        # initial connect — we only fire reconnect listeners after a
        # drop has happened at least once.
        has_been_open = False
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if not cap.isOpened():
                logger.warning(
                    "cam %d: RTSP open failed (%s) — retrying in %.1fs",
                    self.camera_id, self.safe_label(), backoff,
                )
                cap.release()
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, self._max_backoff_s)
                continue

            logger.info(
                "cam %d: RTSP grabber connected (%s, fps cap %.1f)",
                self.camera_id, self.safe_label(), self._target_fps,
            )
            if has_been_open:
                self._fire_listeners(self._reconnect_listeners, "reconnect")
            backoff = self._reconnect_backoff_s
            self._read_loop(cap)
            cap.release()
            has_been_open = True
            if self._stop.is_set():
                break
            self._fire_listeners(self._disrupt_listeners, "disrupt")
            logger.warning(
                "cam %d: RTSP stream dropped — reconnecting in %.1fs",
                self.camera_id, backoff,
            )
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, self._max_backoff_s)
        logger.info("cam %d: RTSP grabber stopped", self.camera_id)

    def _fire_listeners(
        self, listeners: list[Callable[[int], None]], label: str,
    ) -> None:
        # Hand off to a one-shot daemon thread so a slow listener (e.g.
        # a PTZ goto that takes seconds) doesn't block the grabber's
        # own reconnect/read loop.
        if not listeners:
            return
        for fn in list(listeners):
            t = threading.Thread(
                target=self._safe_run_listener,
                args=(fn, label),
                name=f"rtsp-{label}-{self.camera_id}",
                daemon=True,
            )
            t.start()

    def _safe_run_listener(
        self, fn: Callable[[int], None], label: str,
    ) -> None:
        try:
            fn(self.camera_id)
        except Exception:
            logger.exception(
                "cam %d: RTSP %s listener crashed", self.camera_id, label,
            )

    def _read_loop(self, cap) -> None:
        last_forward = 0.0
        consecutive_failures = 0
        while not self._stop.is_set():
            if not cap.grab():
                consecutive_failures += 1
                if consecutive_failures >= 30:
                    return
                if self._stop.wait(0.1):
                    return
                continue
            consecutive_failures = 0
            now = time.monotonic()
            if (now - last_forward) < self._min_interval:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            last_forward = now
            self._fan_out(frame)

    def _fan_out(self, frame: "np.ndarray") -> None:
        with self._consumers_lock:
            consumers = list(self._consumers)
        for fn in consumers:
            try:
                fn(frame)
            except Exception:
                logger.exception("cam %d: grabber consumer crashed",
                                 self.camera_id)


def build_grabbers(
    rtsp_urls: dict[int, str],
    *,
    target_fps: float = 8.0,
    prefer_tcp: bool = True,
) -> dict[int, RtspFrameGrabber]:
    """Construct one grabber per active camera URL.

    `prefer_tcp` adds an `OPENCV_FFMPEG_CAPTURE_OPTIONS` hint so
    OpenCV's ffmpeg backend negotiates RTSP-over-TCP, which is steadier
    than UDP on a lossy LAN. Only set if no operator override is present.
    """
    if prefer_tcp and not os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    out: dict[int, RtspFrameGrabber] = {}
    for cam_id, url in rtsp_urls.items():
        if not url:
            continue
        out[cam_id] = RtspFrameGrabber(
            camera_id=cam_id,
            rtsp_url=url,
            target_fps=target_fps,
        )
    return out
