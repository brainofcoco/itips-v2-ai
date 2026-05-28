"""Real-time "something is happening" feed for the Live page.

Distinct from two neighbours:

  * `SensorEventTap` — only AX PRO sensor events, with dispatcher outcome.
  * `AlertEngine.history()` — incident-level records (post-decision).

`ActivityTap` sits *earlier* than both: it's the lightweight "a line was
just crossed / a region was just entered / a sensor just tripped"
signal, published the instant a detection happens and before any
OpenAI validation or incident packaging. The Live page polls it to
show a transient per-camera status ("⚠ line crossed · evaluating…")
so an operator sees activity immediately, not after the heavy pipeline
finishes.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Optional


class ActivityTap:
    def __init__(self, capacity: int = 200) -> None:
        self._lock = threading.Lock()
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counter = 0

    def publish(
        self,
        *,
        camera_id: int,
        kind: str,                 # line_crossing | intrusion | loitering | sensor
        label: str = "",           # human label for the UI
        zone_id: Optional[Any] = None,
        zone_name: str = "",
        detail: Optional[dict[str, Any]] = None,
        status: str = "detected",  # detected | evaluating | …
        source: str = "behavior",  # behavior | camera | axpro
    ) -> dict[str, Any]:
        with self._lock:
            self._counter += 1
            entry = {
                "seq": self._counter,
                "camera_id": int(camera_id),
                "kind": kind,
                "label": label or _default_label(kind),
                "zone_id": zone_id,
                "zone_name": zone_name,
                "detail": detail or {},
                "status": status,
                "source": source,
                "ts": time.time(),
            }
            self._buf.append(entry)
            return entry

    def recent(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        if limit is not None:
            items = items[-limit:]
        return list(reversed(items))   # newest first

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


def _default_label(kind: str) -> str:
    return {
        "line_crossing": "Line crossing",
        "intrusion": "Region intrusion",
        "loitering": "Loitering",
        "sensor": "Sensor trip",
    }.get(kind, kind)
