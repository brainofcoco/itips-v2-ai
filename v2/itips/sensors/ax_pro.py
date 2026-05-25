"""Hikvision AX PRO wireless hub listener.

Ported from V1 with the same polling cadence. The PRD-preferred mode is
event-subscription on the hub's open API; switching to that is a Phase 1
ticket once we confirm the hub firmware supports it on the POC SKU.

The listener runs in its own thread. On every poll it diffs the current
zone snapshot against the previous one and fires `on_event` callbacks
for state changes only — never duplicates.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


_DETECTOR_TYPE_MAP = {
    "passiveInfraredDetector": "PIR",
    "magneticContact": "doorContact",
    "vibrationDetector": "vibration",
    "smokeDetector": "smoke",
    "motionDetector": "PIR",
}


@dataclass
class SensorEvent:
    event_type: str
    zone_id: int
    zone_name: str
    event_state: str
    timestamp: str
    raw: dict


class AXProListener(threading.Thread):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        poll_interval_ms: int = 500,
        on_event: Optional[Callable[[SensorEvent], None]] = None,
    ) -> None:
        super().__init__(name="sensor-axpro", daemon=True)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._poll_interval = max(poll_interval_ms / 1000, 0.1)
        self._on_event = on_event
        self._client = None
        self._stop = threading.Event()
        self._previous: dict[int, dict] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if not self._host:
            logger.warning("AX PRO host not configured; sensor listener idle.")
            return
        try:
            from hikaxpro import HikAxPro
        except ImportError:
            logger.error("hikaxpro not installed; sensor listener cannot start.")
            return

        while not self._stop.is_set():
            try:
                if self._client is None:
                    self._client = HikAxPro(self._host, self._username, self._password)
                    self._client.connect()
                    logger.info("AX PRO connected at %s", self._host)
                self._poll_once()
            except Exception:
                logger.exception("AX PRO poll failed; backing off")
                self._client = None
                time.sleep(5.0)
                continue
            time.sleep(self._poll_interval)

        logger.info("AX PRO listener stopped.")

    def _poll_once(self) -> None:
        try:
            zones = self._client.zone_status()  # library-specific
        except Exception:
            logger.exception("AX PRO zone_status failed")
            return

        for zone in zones or []:
            zone_id = int(zone.get("id", 0))
            detector_type = zone.get("detector_type", "")
            kind = _DETECTOR_TYPE_MAP.get(detector_type, "unknown")
            state = "alarm" if zone.get("alarm", False) else "normal"
            previous = self._previous.get(zone_id, {})
            if previous.get("state") == state:
                continue
            self._previous[zone_id] = {"state": state}
            event = SensorEvent(
                event_type=kind,
                zone_id=zone_id,
                zone_name=zone.get("name", f"zone-{zone_id}"),
                event_state=state,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                raw=zone,
            )
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception:
                    logger.exception("on_event handler raised")
