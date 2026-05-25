"""RTSP reader thread — one per camera.

Keeps the latest decoded frame in a single slot; consumers read with
no locking and accept that they may see a stale frame. The reader
auto-reconnects on disconnect with exponential-backoff.

Ported from V1 with two cleanups:
  - The codec-probe is delegated to OpenCV's stream caps via a short
    timeout-bounded probe call.
  - URL credentials are never logged.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np

from itips.camera.pipeline import build_gstreamer_pipeline, detect_backend

logger = logging.getLogger(__name__)

_BACKEND = detect_backend()
logger.info("OpenCV video backend: %s", _BACKEND)


class RTSPReader:
    """Continuously reads frames from a single RTSP stream in a background thread.

    Attribute `frame` is the latest decoded frame or None. Callers do not
    block; the reader simply skips publishing if a frame is unavailable.
    """

    def __init__(self, rtsp_url: str, camera_id: int, max_width: int = 0) -> None:
        self.rtsp_url = rtsp_url
        self.camera_id = camera_id
        self.max_width = max_width
        self.frame: Optional[np.ndarray] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"rtsp-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)

    def update_url(self, rtsp_url: str) -> None:
        self.rtsp_url = rtsp_url
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    # ─── internals ────────────────────────────────────────────────

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not self.rtsp_url:
                self._sleep(5)
                continue

            cap = self._open()
            if cap is None or not cap.isOpened():
                logger.warning("Camera %d: open failed (backoff=%.1fs)", self.camera_id, backoff)
                self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            self._cap = cap
            self._read_loop(cap)

    def _read_loop(self, cap: cv2.VideoCapture) -> None:
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("Camera %d: read failure; reconnecting...", self.camera_id)
                cap.release()
                return
            if self.max_width and frame.shape[1] > self.max_width:
                scale = self.max_width / frame.shape[1]
                frame = cv2.resize(frame, (self.max_width, int(frame.shape[0] * scale)))
            self.frame = frame

    def _open(self) -> Optional[cv2.VideoCapture]:
        if _BACKEND == "gstreamer":
            pipeline = build_gstreamer_pipeline(self.rtsp_url)
            return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000"
        return cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._stop.is_set():
            time.sleep(0.1)
