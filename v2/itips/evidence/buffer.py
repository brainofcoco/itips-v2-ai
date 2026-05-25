"""Per-camera pre-event ring buffer.

Keeps the last N seconds of frames in memory. On `start_incident`, the
buffer's contents are written to disk as the pre-event video and the
buffer is detached from the live capture so subsequent frames go to a
post-event clip instead.

POC default: 60 seconds. PRD default: 900 seconds (15 minutes). The
larger value is feasible with a 4 K H.265 source on the NVMe (mode swap
to disk-backed buffer in Phase 1).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

import numpy as np


class RingBuffer:
    """Fixed-time-window FIFO of (monotonic_seconds, frame) tuples.

    Thread-safe. Producers append; consumers snapshot. Snapshots are a
    shallow list copy — the frames themselves are not copied, so callers
    must treat them as immutable (which they already are in V2).
    """

    def __init__(self, window_seconds: float) -> None:
        self._window = float(window_seconds)
        self._frames: deque[tuple[float, np.ndarray]] = deque()
        self._lock = threading.Lock()

    def append(self, frame: np.ndarray, *, now: float | None = None) -> None:
        ts = now if now is not None else time.monotonic()
        with self._lock:
            self._frames.append((ts, frame))
            self._evict_locked(ts)

    def snapshot(self) -> list[tuple[float, np.ndarray]]:
        with self._lock:
            self._evict_locked(time.monotonic())
            return list(self._frames)

    def size(self) -> int:
        with self._lock:
            return len(self._frames)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def _evict_locked(self, now: float) -> None:
        threshold = now - self._window
        frames = self._frames
        while frames and frames[0][0] < threshold:
            frames.popleft()
