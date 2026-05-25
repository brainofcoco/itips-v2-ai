"""Short-lived per-track whitelist + maintenance-window enforcement.

Two responsibilities:
  1. After a track's face is matched to a known person, suppress further
     alerts on that track for `ttl_seconds`. Stops a registered staff
     member from creating a chain of alerts while they walk the compound.
  2. Apply maintenance windows pushed via the B3 endpoint. While a window
     is active, the named person is disarmed at the named site for the
     named duration. Deterrence and RAPID are suppressed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Authorization:
    expires_at: float
    name: str


@dataclass
class _MaintenanceWindow:
    window_id: str
    person_id: str
    start_epoch: float
    end_epoch: float


class FaceAuthorizer:
    def __init__(self, ttl_seconds: int = 120) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._track_auths: dict[tuple[int, int], _Authorization] = {}
        self._windows: dict[str, _MaintenanceWindow] = {}

    # ─── per-track TTL whitelist ───────────────────────────────────

    def authorize(self, camera_id: int, track_id: int, name: str) -> None:
        with self._lock:
            self._track_auths[(camera_id, track_id)] = _Authorization(
                expires_at=time.monotonic() + self._ttl, name=name
            )

    def is_authorized(self, camera_id: int, track_id: int) -> bool:
        with self._lock:
            auth = self._track_auths.get((camera_id, track_id))
            if not auth:
                return False
            if auth.expires_at < time.monotonic():
                del self._track_auths[(camera_id, track_id)]
                return False
            return True

    # ─── maintenance windows (PRD §4.5 REQ-PDB-05, B3 inbound) ────

    def apply_maintenance_window(self, payload: dict[str, Any]) -> None:
        action = payload["action"]
        window_id = str(payload["window_id"])
        if action == "disarm":
            self._windows.pop(window_id, None)
            return
        self._windows[window_id] = _MaintenanceWindow(
            window_id=window_id,
            person_id=str(payload["person_id"]),
            start_epoch=_parse_iso(payload["start_utc"]),
            end_epoch=_parse_iso(payload["end_utc"]),
        )

    def is_in_maintenance(self, person_id: str) -> bool:
        now = time.time()
        with self._lock:
            for win in self._windows.values():
                if win.person_id == person_id and win.start_epoch <= now <= win.end_epoch:
                    return True
        return False


def _parse_iso(value: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
