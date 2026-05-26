"""Shared types for the sensor pipeline.

`SensorEvent` is the lingua franca between the (future) AX PRO hub
listener, the manual dashboard test-trigger, and the
`SensorDispatcher`. Anything that produces sensor events emits this
shape; the dispatcher only consumes it. That decoupling lets us
ship Phase 1 with simulated events and wire the real AX PRO listener
in Phase 2 without touching the dispatcher.

`SensorEventTap` is a per-process ring buffer of the most recent
events — the dashboard reads from it to show "what fired and when"
without having to plumb each fired event through SSE.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class SensorEvent:
    """One alarm from a wireless sensor on the AX PRO hub.

    `source` distinguishes a real hub event ("axpro") from a manual
    operator-injected test ("simulate") so the alert downstream knows
    whether to suppress UI fanfare during testing.
    """

    zone_id: int
    event_type: str               # "PIR" / "vibration" / "doorContact" / "pircam" / …
    event_state: str = "alarm"    # "alarm" / "tamper" / "restore" / …
    zone_name: str = ""           # human label from the hub if known
    source: str = "axpro"         # "axpro" | "simulate"
    received_ts: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class SensorEventTap:
    """Bounded ring buffer of recent sensor events, for the dashboard.

    Each event the dispatcher receives — whether from the AX PRO hub
    or the dashboard test button — also lands here so operators can
    see a live audit trail in the Sensors tab. Capped so a stuck-open
    sensor can't grow it unbounded.
    """

    def __init__(self, capacity: int = 200) -> None:
        self._lock = threading.Lock()
        self._buf: deque[dict] = deque(maxlen=capacity)
        self._counter = 0

    def publish(self, event: SensorEvent, *, outcome: Optional[dict] = None) -> None:
        """Append. `outcome` is the dispatcher's verdict (matched / intruder /
        unverified / error) plus any details — surfaced in the recent-events
        list so the operator can see end-to-end what happened."""
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
        return list(reversed(items))   # newest first

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
