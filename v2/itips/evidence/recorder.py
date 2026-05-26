"""Per-camera incident video recorder.

Lifecycle:
  - `feed(frame)`               every camera tick (also when no incident is active)
  - `begin(incident_id, dir)`   when an incident opens — snapshots the ring
                                buffer to `Video_cam{N}_pre.mp4` and opens
                                `Video_cam{N}_post.mp4` for the live tail
  - `finish()`                  when the incident is finalized — closes the
                                post writer, attaches both files to the
                                packager, returns

Design notes
------------
The ring buffer is fed every frame whether or not an incident is open, so
when one fires we already have the pre-event seconds in memory.

Pre.mp4 writing happens on a one-shot helper thread so the engine doesn't
wait on disk; the helper signals completion via an Event that finish() can
wait on before attaching.

Post.mp4 is written inline from `feed()`. cv2.VideoWriter.write() is
single-digit-millisecond on local NVMe and small frames, so blocking the
camera worker briefly is fine.

The playback fps is derived from observed inter-frame deltas in the ring
buffer at begin(). This keeps real-time playback correct even on slow CPU
sim mode (where the worker tick can fall to 3–5 fps).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from itips.evidence.buffer import RingBuffer
from itips.evidence.video_writer import open_writer

logger = logging.getLogger(__name__)

_DEFAULT_FPS = 12.0
_MIN_FPS = 4.0
_MAX_FPS = 30.0


@dataclass
class _Active:
    incident_id: str
    pre_path: Path
    post_path: Path
    post_writer: Optional[cv2.VideoWriter]
    fps: float
    frame_size: tuple[int, int]   # (width, height)
    pre_done: threading.Event
    started_at: float


class IncidentRecorder:
    """Holds the pre-event buffer; encodes pre+post MP4s for one camera."""

    def __init__(
        self,
        *,
        camera_id: int,
        packager,
        pre_event_seconds: int,
        post_event_seconds: int,
    ) -> None:
        self.camera_id = camera_id
        self._packager = packager
        self._pre_seconds = pre_event_seconds
        self._post_seconds = post_event_seconds
        self._buffer = RingBuffer(pre_event_seconds)
        self._lock = threading.Lock()
        self._active: Optional[_Active] = None
        self._post_started_at = 0.0

    # ─── public surface ────────────────────────────────────────────

    def feed(self, frame: np.ndarray) -> None:
        if frame is None or frame.size == 0:
            return
        # Copy because the camera worker may mutate or release the array.
        self._buffer.append(frame.copy())
        with self._lock:
            active = self._active
        if active is None or active.post_writer is None:
            return
        if (time.monotonic() - self._post_started_at) > self._post_seconds:
            # Auto-close post writer once we've captured the tail.
            self._close_post_writer()
            return
        try:
            active.post_writer.write(frame)
        except Exception:
            logger.exception("cam%d: post-event write failed", self.camera_id)

    def begin(self, incident_id: str, package_dir: Path) -> Optional[_Active]:
        with self._lock:
            if self._active is not None:
                return self._active
            snapshot = self._buffer.snapshot()
            if not snapshot:
                logger.info("cam%d: incident %s opened with empty pre-buffer",
                            self.camera_id, incident_id)
            fps, size = self._derive_fps_and_size(snapshot)
            pre_path = package_dir / f"Video_cam{self.camera_id}_pre.mp4"
            post_path = package_dir / f"Video_cam{self.camera_id}_post.mp4"
            pre_done = threading.Event()
            post_writer = self._open_writer(post_path, fps, size)
            active = _Active(
                incident_id=incident_id,
                pre_path=pre_path,
                post_path=post_path,
                post_writer=post_writer,
                fps=fps,
                frame_size=size,
                pre_done=pre_done,
                started_at=time.monotonic(),
            )
            self._active = active
            self._post_started_at = active.started_at
        threading.Thread(
            target=self._write_pre_async,
            args=(active, snapshot),
            name=f"recorder-pre-cam{self.camera_id}",
            daemon=True,
        ).start()
        logger.info("cam%d: recording incident %s — pre=%ds, post=%ds, fps=%.1f",
                    self.camera_id, incident_id, self._pre_seconds, self._post_seconds, fps)
        return active

    def finish(self, *, attach: bool = True, wait_timeout: float = 30.0) -> Optional[Path]:
        with self._lock:
            active = self._active
            self._active = None
        if active is None:
            return None
        self._close_post_writer_with(active)
        if not active.pre_done.wait(timeout=wait_timeout):
            logger.warning("cam%d: pre-event writer did not finish within %.0fs",
                           self.camera_id, wait_timeout)
        if attach:
            for path, kind in [(active.pre_path, "video_pre"),
                               (active.post_path, "video_post")]:
                if path.exists() and path.stat().st_size > 0:
                    self._packager.attach_file(active.incident_id, path, kind=kind)
        return active.pre_path.parent

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active is not None

    # ─── internals ────────────────────────────────────────────────

    def _derive_fps_and_size(self, snapshot: list[tuple[float, np.ndarray]]):
        if not snapshot:
            return _DEFAULT_FPS, (1280, 720)
        h, w = snapshot[-1][1].shape[:2]
        if len(snapshot) < 4:
            return _DEFAULT_FPS, (w, h)
        ts = [t for t, _ in snapshot]
        diffs = [b - a for a, b in zip(ts[:-1], ts[1:]) if b > a]
        if not diffs:
            return _DEFAULT_FPS, (w, h)
        median = sorted(diffs)[len(diffs) // 2]
        fps = 1.0 / median if median > 0 else _DEFAULT_FPS
        fps = max(_MIN_FPS, min(_MAX_FPS, fps))
        return float(fps), (w, h)

    def _open_writer(self, path: Path, fps: float, size: tuple[int, int]):
        # H.265 via ffmpeg when available, cv2/mp4v fallback otherwise.
        writer = open_writer(path, fps=fps, size=size)
        if not writer.isOpened():
            logger.error("cam%d: could not open writer at %s", self.camera_id, path)
            return None
        return writer

    def _write_pre_async(self, active: _Active, snapshot: list[tuple[float, np.ndarray]]) -> None:
        try:
            writer = self._open_writer(active.pre_path, active.fps, active.frame_size)
            if writer is None:
                return
            for _, frame in snapshot:
                if frame.shape[1] != active.frame_size[0] or frame.shape[0] != active.frame_size[1]:
                    # Skip frames captured at a different resolution
                    continue
                writer.write(frame)
            writer.release()
            logger.info("cam%d: pre-event clip written → %s (%d frames)",
                        self.camera_id, active.pre_path.name, len(snapshot))
        except Exception:
            logger.exception("cam%d: pre-event encoder failed", self.camera_id)
        finally:
            active.pre_done.set()

    def _close_post_writer(self) -> None:
        with self._lock:
            active = self._active
        if active is not None:
            self._close_post_writer_with(active)

    def _close_post_writer_with(self, active: _Active) -> None:
        writer = active.post_writer
        if writer is None:
            return
        active.post_writer = None
        try:
            writer.release()
        except Exception:
            logger.exception("cam%d: post writer close failed", self.camera_id)
