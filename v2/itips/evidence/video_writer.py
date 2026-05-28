"""Video writer abstraction — H.265 via ffmpeg, falls back to cv2/mp4v.

PRD §4.3 REQ-EV-01 mandates H.265 for evidence clips. Default encoder
is libx265 (CPU); operator sets ITIPS_VIDEO_ENCODER=hevc_nvenc on
Jetson to use NVENC hardware HEVC instead. If ffmpeg isn't on PATH at
all, we degrade to cv2/mp4v (H.264) with a loud warning — the package
still seals and signs, just doesn't meet the codec spec.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def open_writer(path: Path, fps: float, size: tuple[int, int],
                *, encoder: Optional[str] = None):
    """Return a writer with cv2-compatible .write() / .release() / .isOpened().

    `encoder` overrides the default (env `ITIPS_VIDEO_ENCODER`, else libx265).
    Pass `libx264` for clips that must play in a browser `<video>` — HEVC/MP4
    won't decode in Chrome/Firefox."""
    enc = encoder or os.environ.get("ITIPS_VIDEO_ENCODER", "libx265")
    if _ffmpeg_available():
        try:
            return _FfmpegWriter(path, fps=fps, size=size, encoder=enc)
        except Exception:
            logger.exception("ffmpeg writer failed to open %s — falling back to cv2", path)
    return _Cv2Mp4vWriter(path, fps=fps, size=size)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class _FfmpegWriter:
    """Pipes raw BGR frames into an ffmpeg subprocess transcoding to MP4.

    Encoder is H.265 by default (PRD §4.3); pass `libx264`/`h264_nvenc` for a
    browser-playable clip."""

    def __init__(self, path: Path, *, fps: float, size: tuple[int, int],
                 encoder: str) -> None:
        w, h = int(size[0]), int(size[1])
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-loglevel", "warning",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}",
            "-r", f"{max(1.0, float(fps)):.2f}",
            "-i", "pipe:0",
            "-c:v", encoder,
        ]
        # Each encoder takes different rate/quality knobs.
        if encoder == "libx265":
            cmd += ["-preset", "ultrafast", "-crf", "26"]
        elif encoder == "hevc_nvenc":
            cmd += ["-preset", "p4", "-cq", "26"]
        elif encoder == "libx264":
            cmd += ["-preset", "veryfast", "-crf", "23"]
        elif encoder == "h264_nvenc":
            cmd += ["-preset", "p4", "-cq", "23"]
        cmd += [
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-f", "mp4",
            str(path),
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self._encoder = encoder
        self._opened = self._proc.poll() is None
        self._broken = False

    def isOpened(self) -> bool:  # noqa: N802 — cv2-compatible
        return self._opened and not self._broken

    def write(self, frame: np.ndarray) -> None:
        if self._broken or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            self._broken = True
            self._drain_stderr("ffmpeg pipe broke")

    def release(self) -> None:
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        try:
            self._proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=2.0)
        if self._proc.returncode not in (0, None):
            self._drain_stderr(f"ffmpeg exit={self._proc.returncode}")

    def _drain_stderr(self, prefix: str) -> None:
        if self._proc.stderr is None:
            return
        try:
            err = self._proc.stderr.read().decode("utf-8", errors="replace")
            if err.strip():
                logger.warning("%s for %s: %s", prefix, self._path.name, err.strip())
        except Exception:
            pass


class _Cv2Mp4vWriter:
    """Last-resort writer — H.264 / mp4v. NOT PRD-compliant; warns loudly."""

    def __init__(self, path: Path, *, fps: float, size: tuple[int, int]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._inner = cv2.VideoWriter(str(path), fourcc, fps, size)
        if not self._inner.isOpened():
            logger.error("cv2 fallback writer could not open %s", path)
        else:
            logger.warning(
                "ffmpeg not on PATH; falling back to cv2 mp4v (H.264) for %s — "
                "install ffmpeg in the image to satisfy PRD §4.3 REQ-EV-01 H.265",
                path.name,
            )

    def isOpened(self) -> bool:  # noqa: N802
        return self._inner.isOpened()

    def write(self, frame: np.ndarray) -> None:
        self._inner.write(frame)

    def release(self) -> None:
        self._inner.release()


def transcode_or_copy(src: Path, dst: Path, *,
                      start_s: float = 0.0,
                      duration_s: Optional[float] = None) -> bool:
    """Cut a sub-clip with `ffmpeg -ss start -t dur` — codec-copy if possible.

    Used for the highlight clip. Returns False when ffmpeg isn't on PATH;
    caller decides whether to skip or fall back to no-highlight."""
    if not _ffmpeg_available():
        return False
    cmd = ["ffmpeg", "-y", "-loglevel", "warning", "-ss", f"{start_s:.2f}"]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.2f}"]
    cmd += ["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60.0)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg highlight transcode timed out for %s", src)
        return False
    if r.returncode != 0:
        logger.warning("ffmpeg highlight: %s",
                       r.stderr.decode("utf-8", errors="replace").strip())
        return False
    return True
