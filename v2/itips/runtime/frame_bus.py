"""Lock-free latest-frame bus between camera workers and the public API.

Each camera publishes its newest annotated frame here; the MJPEG endpoint
reads the latest snapshot. Slow consumers never block fast producers —
they just see an older frame.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class FrameSnapshot:
    camera_id: int
    raw: Optional[np.ndarray] = None
    annotated: Optional[np.ndarray] = None
    monotonic_ns: int = 0
    preset_id: str = "default"


class FrameBus:
    """One slot per camera; writers replace, readers copy-by-reference.

    `numpy.ndarray` is treated as immutable by convention — workers must
    produce a fresh array per publish, never mutate in place.
    """

    def __init__(self) -> None:
        self._slots: dict[int, FrameSnapshot] = {}
        self._lock = threading.Lock()

    def publish(self, snapshot: FrameSnapshot) -> None:
        with self._lock:
            self._slots[snapshot.camera_id] = snapshot

    def latest(self, camera_id: int) -> Optional[FrameSnapshot]:
        with self._lock:
            return self._slots.get(camera_id)

    def active_cameras(self) -> list[int]:
        with self._lock:
            return sorted(self._slots.keys())
