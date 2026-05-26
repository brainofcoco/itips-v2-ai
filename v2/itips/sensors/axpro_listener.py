"""Hikvision AX PRO Hub listener — drains alarm-edge events into the dispatcher.

What this does, in one sentence: poll `zone_status()` on the AX PRO
hub every ~500 ms, notice when any zone's `alarm` flag transitions
false→true, build an `itips.sensors.sensor_event.SensorEvent`, and
hand it to the `SensorDispatcher`. From there the existing pipeline
runs — pan PTZ → snapshot → face validate → AlertEngine.

The dispatcher already has unit-tested branches for every outcome, so
this module only needs to do three things well: stay connected to the
hub, emit *only* on the rising edge of an alarm (one event per alarm,
not one per poll while the alarm is held), and surface its connection
state to the dashboard.

Design choices that come straight from the constraints:

* **Lazy `hikaxpro` import.** Same posture as the ML engines: the v2
  baseline runs without the lib. If `hikaxpro` isn't installed the
  caller gets `AxProUnavailable` on `start()` and the listener stays
  off — the dashboard's Simulate button still drives the full
  dispatcher pipeline, so testing without a hub is unaffected.
* **Alarm-edge only (Phase 1).** The reference repo also fires on
  tamper changes and arbitrary `status_*` transitions. Most of those
  are noise (zones flapping in and out of `normal` as battery levels
  shift, tamper "cleared" events from someone reseating a sensor
  during install). Phase 1 keeps the surface tight: an alarm went from
  not-firing to firing → dispatch one event. Tamper / status churn
  is logged at DEBUG only.
* **No arm-state gating.** We *report* `is_armed` to the dashboard so
  the operator knows whether the hub is live, but we don't suppress
  dispatches when it reads disarmed — the reference repo doesn't
  either, and during installation/walk-tests you actively need the
  pipeline to run while the hub is disarmed. Phase 2 can add a
  configurable gate if false alarms become a real problem.
* **Reconnect with backoff, never crash.** Any hub-side exception
  drops the client, sleeps `reconnect_backoff_s`, and tries again.
  A flaky hub or a network hiccup at the tower must not take down
  the whole edge node.
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
    """`hikaxpro` not installed. Catch in app.py to disable the listener
    without taking down the rest of the edge node."""


# hikaxpro reports `detectorType` strings that are accurate but
# verbose; the dispatcher and the dashboard work in shorter symbols.
# Mirrors the reference repo's table — keep them in sync if Hikvision
# adds new detector classes upstream.
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
        # zone_id → bool (last-seen alarm flag). Without this we'd
        # re-fire on every poll while the alarm is held. The whole
        # point of the state map is to compress hub-side poll noise
        # into rising-edge events.
        self._alarm_state: dict[int, bool] = {}
        # Updated by the periodic arm-state poll; consumed by the
        # dashboard status endpoint. Default False so a never-polled
        # hub reads "disarmed" rather than misleadingly armed.
        self._is_armed = False
        self._connected = False
        self._last_error: Optional[str] = None

    # ─── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Resolve the `hikaxpro` import and start the polling thread.

        Non-raising: a missing `hikaxpro` lib logs a warning, sets a
        last_error reason, and leaves the thread unstarted. The
        orchestrator's start-all loop is naive, so any exception here
        kills boot — silent degradation is the right default and the
        listener-status endpoint still reports the missing-lib reason.
        """
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
                # The hub's HTTP API can throw a wide variety of errors
                # depending on which firmware revision is talking. We
                # don't want a transient one to permanently break the
                # listener — drop the client, sleep, reconnect.
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
            self._client = HikAxPro(self._host, self._username, self._password)
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
            # Don't let arm-state poll errors disturb the alarm poll;
            # caller already wraps the broader loop with reconnect.
            logger.debug("AxProListener: subsystem_status() raised — leaving arm state stale")
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
        for entry in (data.get("ZoneList") or []):
            zone = entry.get("Zone", {}) if isinstance(entry, dict) else {}
            try:
                zone_id = int(zone.get("id"))
            except (TypeError, ValueError):
                continue
            alarm = bool(zone.get("alarm", False))
            prev = self._alarm_state.get(zone_id)
            # First time we see a zone, just record state — don't fire
            # an "alarm" event on startup for sensors that already had
            # an unresolved alarm before the listener booted.
            if prev is None:
                self._alarm_state[zone_id] = alarm
                continue
            self._alarm_state[zone_id] = alarm
            if alarm and not prev:
                self._fire_alarm(zone_id, zone)
            elif prev and not alarm:
                logger.debug("AxProListener: zone %d cleared", zone_id)

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
