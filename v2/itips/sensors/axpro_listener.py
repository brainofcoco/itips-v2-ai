"""AX PRO hub listener — polls zones, dispatches alarm-edge events.

Alarm-edge only: one event per false→true transition, not per poll.
Tamper / status flapping is DEBUG-logged but not dispatched.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

from itips.sensors.sensor_event import SensorEvent

if TYPE_CHECKING:
    from itips.runtime.sensor_dispatcher import SensorDispatcher

logger = logging.getLogger(__name__)


class AxProUnavailable(RuntimeError):
    """`hikaxpro` not installed. Caller disables the listener and the
    Simulate trigger still drives the dispatcher."""


# Map hikaxpro's verbose `detectorType` strings to dispatcher/UI symbols.
_DETECTOR_TYPE_MAP: dict[str, str] = {
    "passiveInfraredDetector": "PIR",
    "magneticContact":         "doorContact",
    "vibrationDetector":       "vibration",
    "smokeDetector":           "smoke",
    "motionDetector":          "PIR",
    "pircam":                  "PIR",
    "wirelesTriTechDetector":  "PIR",
}


class AxProListener(threading.Thread):
    """Long-poll thread that turns AX PRO hub alarms into SensorEvents."""

    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        dispatcher: "SensorDispatcher",
        poll_interval_s: float = 0.5,
        arm_poll_interval_s: float = 2.0,
        reconnect_backoff_s: float = 5.0,
    ) -> None:
        super().__init__(name="axpro-listener", daemon=True)
        self._host = host
        self._username = username
        self._password = password
        self._dispatcher = dispatcher
        self._poll_interval_s = float(poll_interval_s)
        self._arm_poll_interval_s = float(arm_poll_interval_s)
        self._reconnect_backoff_s = float(reconnect_backoff_s)

        self._stop_event = threading.Event()
        self._client = None
        # zone_id → last-seen alarm flag. Compresses poll noise into edges.
        self._alarm_state: dict[int, bool] = {}
        # Default False — never-polled hub reads disarmed, not misleadingly armed.
        self._is_armed = False
        self._connected = False
        self._last_error: Optional[str] = None
        # First successful poll is logged at INFO so operators can see
        # at a glance which zones the hub exposes (and whether their
        # initial alarm flag is what they expect).
        self._first_poll_logged = False

    # ─── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Non-raising — orchestrator's start-all loop is naive, so any
        exception here kills boot. Missing lib → log + leave inert."""
        try:
            from hikaxpro import HikAxPro  # noqa: F401
        except ImportError as exc:
            self._last_error = f"hikaxpro not installed: {exc}"
            self._connected = False
            logger.warning(
                "AxProListener disabled — %s. Set ITIPS_AXPRO_HOST to empty "
                "to silence this, or install the ml extras.",
                self._last_error,
            )
            return
        if self.is_alive():
            return
        super().start()

    def stop(self) -> None:
        self._stop_event.set()

    # ─── status (for the dashboard) ──────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_armed(self) -> bool:
        return self._is_armed

    @property
    def host(self) -> str:
        return self._host

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def get_client(self):
        """Return the authenticated hikaxpro client (or None if not yet
        connected). Sibling services — `AxProAlertStream`,
        `AxProAdmin` — share this session rather than opening their own."""
        return self._client

    def alarm_state_snapshot(self) -> dict[int, bool]:
        """Last-seen alarm flag per zone — what the edge detector uses
        to decide if an event is a fresh false→true transition."""
        return dict(self._alarm_state)

    def fetch_raw_zone_status(self) -> dict:
        """Pull the hub's raw zone payload for diagnostic display.

        Operators use this when a real sensor activation isn't showing
        up in the events feed — comparing raw hub state against
        `alarm_state_snapshot()` shows whether the issue is field-name
        drift in the hub response, an arming-mode block, or a true
        signal-path break.
        """
        if self._client is None:
            return {"error": "not connected", "last_error": self._last_error}
        try:
            return self._client.zone_status() or {}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{exc.__class__.__name__}: {exc}"}

    # ─── polling loop ─────────────────────────────────────────────────

    def run(self) -> None:
        logger.info(
            "AxProListener starting — host=%s poll=%.2fs reconnect_backoff=%.0fs",
            self._host, self._poll_interval_s, self._reconnect_backoff_s,
        )
        last_arm_poll = 0.0
        while not self._stop_event.is_set():
            if self._client is None and not self._connect():
                if self._stop_event.wait(self._reconnect_backoff_s):
                    break
                continue
            try:
                now = time.monotonic()
                if now - last_arm_poll >= self._arm_poll_interval_s:
                    self._refresh_arm_state()
                    last_arm_poll = now
                self._poll_zones()
            except Exception as exc:  # noqa: BLE001
                # Drop client + reconnect on any hub-side error so a
                # firmware quirk doesn't permanently break the listener.
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                self._connected = False
                logger.warning(
                    "AxProListener: hub poll failed (%s) — reconnecting in %.0fs",
                    self._last_error, self._reconnect_backoff_s,
                )
                self._client = None
                if self._stop_event.wait(self._reconnect_backoff_s):
                    break
                continue

            if self._stop_event.wait(self._poll_interval_s):
                break
        logger.info("AxProListener stopped")

    # ─── connect ──────────────────────────────────────────────────────

    def _connect(self) -> bool:
        try:
            from hikaxpro import HikAxPro
        except ImportError as exc:
            self._last_error = f"hikaxpro not installed: {exc}"
            return False
        try:
            client = HikAxPro(self._host, self._username, self._password)
            # HikAxPro.__init__ only stores credentials; the session-id
            # capabilities aren't fetched until connect() runs. Without
            # this call the first zone_status() trips on
            # `NoneType.session_id_version` inside the library.
            if not client.connect():
                self._client = None
                self._connected = False
                self._last_error = "login rejected (connect() returned False)"
                logger.warning(
                    "AxProListener: login rejected by %s — check ITIPS_AXPRO_USERNAME/PASSWORD",
                    self._host,
                )
                return False
            self._client = client
            self._connected = True
            self._last_error = None
            logger.info("AxProListener: connected to %s", self._host)
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{exc.__class__.__name__}: {exc}"
            self._client = None
            self._connected = False
            logger.warning(
                "AxProListener: connect to %s failed (%s)",
                self._host, self._last_error,
            )
            return False

    # ─── arm state ────────────────────────────────────────────────────

    def _refresh_arm_state(self) -> None:
        if self._client is None:
            return
        try:
            data = self._client.subsystem_status() or {}
        except Exception:
            # Caller's loop already handles reconnect; just keep cached value.
            logger.debug("AxProListener: subsystem_status() raised — keeping cached arm state")
            return
        armed = False
        for entry in (data.get("SubSysList") or []):
            sub = entry.get("SubSys", {}) if isinstance(entry, dict) else {}
            arming = str(sub.get("arming", "")).lower()
            if arming and arming != "disarm":
                armed = True
                break
        if armed != self._is_armed:
            logger.info("AxProListener: hub %s",
                        "ARMED" if armed else "DISARMED")
        self._is_armed = armed

    # ─── zones ────────────────────────────────────────────────────────

    def _poll_zones(self) -> None:
        assert self._client is not None
        data = self._client.zone_status() or {}
        zones_seen: list[tuple[int, str, bool]] = []
        for entry in (data.get("ZoneList") or []):
            zone = entry.get("Zone", {}) if isinstance(entry, dict) else {}
            try:
                zone_id = int(zone.get("id"))
            except (TypeError, ValueError):
                continue
            alarm = _zone_in_alarm(zone)
            zones_seen.append((zone_id, str(zone.get("name") or ""), alarm))
            prev = self._alarm_state.get(zone_id)
            # First sighting: just record. Don't replay alarms that
            # were already firing when the listener booted.
            if prev is None:
                self._alarm_state[zone_id] = alarm
                continue
            self._alarm_state[zone_id] = alarm
            if alarm and not prev:
                self._fire_alarm(zone_id, zone)
            elif prev and not alarm:
                logger.debug("AxProListener: zone %d cleared", zone_id)

        if not self._first_poll_logged and zones_seen:
            self._first_poll_logged = True
            summary = ", ".join(
                f"z{zid}={'ALARM' if al else 'idle'}({name})"
                for zid, name, al in zones_seen
            )
            logger.info(
                "AxProListener: first poll OK — %d zone(s): %s",
                len(zones_seen), summary,
            )

    def _fire_alarm(self, zone_id: int, zone: dict) -> None:
        detector_type = str(zone.get("detectorType") or "")
        event_type = _DETECTOR_TYPE_MAP.get(detector_type, detector_type or "unknown")
        zone_name = str(zone.get("name") or f"Zone {zone_id}")
        event = SensorEvent(
            zone_id=zone_id,
            event_type=event_type,
            event_state="alarm",
            zone_name=zone_name,
            source="axpro",
            raw=zone,
        )
        logger.warning("AxProListener: ALARM zone=%d (%s) type=%s",
                       zone_id, zone_name, event_type)
        try:
            self._dispatcher.dispatch(event)
        except Exception:
            logger.exception("AxProListener: dispatch crashed for zone=%d", zone_id)


def _zone_in_alarm(zone: dict) -> bool:
    """Detect 'this zone is currently triggered' across AX PRO firmwares.

    Older firmwares expose a boolean `alarm` field; newer ones moved to
    a `status` string with values like `trigger`, `intrusionTrigger`,
    `tamper`, `restored` (where `restored` means 'back to idle'). We
    accept either path so a firmware update on the hub doesn't silently
    stop sensor events from firing.
    """
    if "alarm" in zone:
        try:
            return bool(zone.get("alarm"))
        except Exception:
            pass
    raw_status = str(zone.get("status") or "").lower()
    if not raw_status:
        return False
    if raw_status in {"trigger", "intrusiontrigger", "alarm", "alarming"}:
        return True
    if "trigger" in raw_status or "alarm" in raw_status:
        return True
    return False
