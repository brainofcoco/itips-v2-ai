"""Raw-event ring buffer for the Test Console.

Every event a Dahua camera emits — including the noise filtered out by
the dispatcher's per-code cooldown — lands here. The Test Console's SSE
endpoint reads from it so operators can watch what the cameras are
actually saying in real time.

The buffer is bounded so a chatty camera (a tree in the wind that fires
VideoMotion every second) can never blow up Jetson memory.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Optional


class EventTap:
    """Thread-safe ring of the last N raw events.

    Producers (dispatchers) call `publish()`. Consumers (SSE handlers)
    call `since(cursor)` and pass the returned `next_cursor` back on the
    next poll — a monotonic counter so we never miss events between polls
    and never re-deliver one we already streamed.
    """

    def __init__(self, capacity: int = 500) -> None:
        self._capacity = capacity
        self._buf: deque[tuple[int, dict[str, Any]]] = deque(maxlen=capacity)
        self._counter = 0
        self._lock = threading.Lock()
        # Permanent set (camera_id, code) → last_seen_seq. Separate from
        # the ring buffer so the health check can answer "has this camera
        # ever fired FaceRecognition?" even after the event aged out.
        self._codes_seen: dict[int, dict[str, int]] = {}

    def publish(self, *, camera_id: int, code: str, action: str,
                index: int, data: dict[str, Any], has_jpeg: bool = False) -> None:
        with self._lock:
            self._counter += 1
            self._buf.append((self._counter, {
                "seq": self._counter,
                "camera_id": camera_id,
                "code": code,
                "action": action,
                "index": index,
                "data": data,
                "has_jpeg": has_jpeg,
                "ts": time.time(),
            }))
            self._codes_seen.setdefault(camera_id, {})[code] = self._counter

    def since(self, cursor: int) -> tuple[list[dict[str, Any]], int]:
        """Return (new_events, next_cursor). `cursor=0` returns everything."""
        with self._lock:
            new_items = [item for seq, item in self._buf if seq > cursor]
            next_cursor = self._counter
        return new_items, next_cursor

    def recent(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Snapshot of the most recent events for non-streaming consumers."""
        with self._lock:
            items = [item for _, item in self._buf]
        if limit is not None:
            items = items[-limit:]
        return items

    def codes_seen_by_camera(self) -> dict[int, dict[str, int]]:
        """For the health check: {camera_id: {event_code: last_seq}}.

        Survives the ring-buffer evicting an old event. Useful for
        answering "has this camera ever produced this event type?"
        """
        with self._lock:
            return {cam: dict(codes) for cam, codes in self._codes_seen.items()}
