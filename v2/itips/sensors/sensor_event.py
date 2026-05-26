"""Shared types for the sensor pipeline — event + recent-events tap.

`source="axpro"` for real hub events, `source="simulate"` for the
dashboard's Simulate trigger; same downstream pipeline either way.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class SensorEvent:
    """One alarm from a wireless sensor on the AX PRO hub."""

    zone_id: int
    event_type: str               # PIR / vibration / doorContact / pircam
    event_state: str = "alarm"    # alarm / tamper / restore
    zone_name: str = ""
    source: str = "axpro"         # axpro | simulate
    received_ts: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class SensorEventTap:
    """Bounded ring buffer of recent sensor events, for the dashboard."""

    def __init__(self, capacity: int = 200) -> None:
        self._lock = threading.Lock()
        self._buf: deque[dict] = deque(maxlen=capacity)
        self._counter = 0

    def publish(self, event: SensorEvent, *, outcome: Optional[dict] = None) -> None:
        """`outcome` is the dispatcher's verdict — surfaced alongside the
        event so the dashboard shows end-to-end what happened."""
        with self._lock:
            self._counter += 1
            self._buf.append({
                "seq": self._counter,
                **event.to_dict(),
                "outcome": outcome or {},
            })

    def recent(self, limit: Optional[int] = None) -> list[dict]:
        with self._lock:
            items = list(self._buf)
        if limit is not None:
            items = items[-limit:]
        return list(reversed(items))

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
