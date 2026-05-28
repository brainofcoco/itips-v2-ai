"""AX PRO real-time event subscription via /ISAPI/Event/notification/alertStream.

The zone-status poller (`AxProListener`) reads a snapshot of every zone
on a 500 ms interval. That works for slow signals like a magnetic
contact's `magnetOpenStatus` (which stays steady while a door is open)
but loses two things that matter:

  • Short pulses. A PIR's `alarm` flag flips for a fraction of a
    second; between polls it's gone.
  • Hub-side events that never land in `zone_status` at all (CID
    events: arm/disarm, tamper, low-battery, sensor offline, panic).

`alertStream` is the canonical Hikvision push channel. It's a
multipart-mixed long-poll: the hub holds the connection open and pushes
each event as a `--boundary` frame containing a JSON body. The frame
arrives within ~200 ms of the physical trigger.

Both listeners run in parallel. The poller still publishes
`magnetOpenStatus` edges (because some firmwares never raise an alarm
event for door-open in stay-armed or zone-config-bypassed states), and
the alertStream feeds everything else. Dedup happens in the
SensorDispatcher via its existing per-zone cooldown.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Optional

from itips.sensors.sensor_event import SensorEvent

logger = logging.getLogger(__name__)


# Common CID (Contact-ID) alarm codes → ITIPS event_type.
# Reference: SIA DC-05 / Hikvision AX PRO documentation. Codes outside
# this table are still surfaced — the raw code goes into event.raw so
# the dispatcher / operator can audit.
_CID_CODE_MAP: dict[int, str] = {
    # 1100s — alarm conditions (the ones that should fire incidents)
    1100: "medical",
    1110: "fire",
    1111: "smoke",
    1120: "panic",
    1130: "burglary",   # generic alarm
    1131: "perimeter",  # perimeter zone tripped
    1132: "intrusion",  # interior zone tripped (PIR etc.)
    1133: "safe",
    1134: "entryExit",
    1137: "tamper",
    1144: "tamper",
    1150: "vibration",
    1154: "waterLeak",
    1158: "highTemperature",
    1159: "lowTemperature",
    1162: "carbonMonoxide",
    1170: "doorContact",
    # 1300s — system trouble (info, not a security alarm)
    1301: "acLoss",
    1302: "lowBattery",
    1351: "telcoLine",
    1380: "sensorTrouble",
    1381: "rfSensorLoss",
    1383: "tamperTrouble",
    1384: "rfSensorLowBattery",
    # 1400s — open/close (arming events)
    1401: "openClose",
    1407: "remoteArmDisarm",
    # 1600+ — periodic test / housekeeping
    1602: "periodicTest",
    1627: "programEnter",
    1628: "programExit",
}


class AxProAlertStream(threading.Thread):
    """Long-poll subscriber that turns alertStream events into SensorEvents.

    Shares the hikaxpro client's session cookie rather than opening its
    own — saves a separate session and lets the existing
    `AxProListener` drive reconnects centrally."""

    def __init__(
        self,
        *,
        host: str,
        client_supplier: Callable[[], Any],
        dispatcher,
        reconnect_backoff_s: float = 5.0,
        read_timeout_s: float = 70.0,
    ) -> None:
        super().__init__(name="axpro-alertstream", daemon=True)
        self._host = host
        self._client_supplier = client_supplier
        self._dispatcher = dispatcher
        self._reconnect_backoff_s = float(reconnect_backoff_s)
        self._read_timeout_s = float(read_timeout_s)
        self._stop_event = threading.Event()
        self._connected = False
        self._last_error: Optional[str] = None
        self._events_received = 0
        # Optional listeners for raw stream events — webhook subsystem
        # binds here so subscribers can opt into the full firehose.
        self._raw_listeners: list[Callable[[dict[str, Any]], None]] = []
        # Parser carry-over: a JSON event part on the wire is often
        # immediately followed by an image/jpeg part containing the
        # alarm picture (PIR-cam devices). We hold the JSON event
        # pending until we see what the next part is — if it's a JPEG
        # we attach the bytes and dispatch; if it's another JSON we
        # dispatch the previous one without a picture and start fresh.
        self._pending_event: Optional[dict[str, Any]] = None

    # ─── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if self.is_alive():
            return
        super().start()

    def stop(self) -> None:
        self._stop_event.set()

    def add_raw_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._raw_listeners.append(listener)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def events_received(self) -> int:
        return self._events_received

    # ─── loop ─────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info("AxProAlertStream starting — host=%s", self._host)
        while not self._stop_event.is_set():
            try:
                self._subscribe_once()
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                self._connected = False
                logger.warning(
                    "AxProAlertStream: stream broke (%s) — reconnecting in %.0fs",
                    self._last_error, self._reconnect_backoff_s,
                )
            if self._stop_event.wait(self._reconnect_backoff_s):
                break
        logger.info("AxProAlertStream stopped")

    def _wait_for_logged_in_client(self, timeout_s: float = 30.0):
        """Poll the listener's client until it's authenticated.

        The alertStream thread starts in parallel with `AxProListener`,
        which performs the SHA-256 challenge handshake against the hub
        on its first poll. Until that lands, `get_client()` returns
        `None` or a client with no session cookie. Waiting here turns
        the previous "stream broke (client not yet logged in)" reconnect
        warning into a silent startup pause.
        """
        deadline = time.monotonic() + timeout_s
        while not self._stop_event.is_set():
            client = self._client_supplier()
            cookie = (
                getattr(client, "_cookie", None) or getattr(client, "cookie", None)
                if client is not None else None
            )
            if client is not None and cookie:
                return cookie
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "timed out waiting for hikaxpro client to log in "
                    f"(after {timeout_s:.0f}s)"
                )
            if self._stop_event.wait(0.25):
                raise RuntimeError("stopped while waiting for hikaxpro login")
        raise RuntimeError("stopped while waiting for hikaxpro login")

    def _subscribe_once(self) -> None:
        import requests
        cookie = self._wait_for_logged_in_client()
        url = f"http://{self._host}/ISAPI/Event/notification/alertStream"
        resp = requests.get(
            url, cookies={"WebSession": cookie},
            timeout=(5, self._read_timeout_s), stream=True,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"alertStream HTTP {resp.status_code} — {resp.text[:120]!r}")
        self._connected = True
        self._last_error = None
        logger.info("AxProAlertStream: subscribed to %s", url)
        # Drive the multipart parser off raw chunks instead of resp.iter_lines
        # because the hub doesn't always honour the documented \r\n
        # separators — boundary regex is more resilient.
        buf = b""
        while not self._stop_event.is_set():
            chunk = resp.raw.read(4096)
            if not chunk:
                if self._stop_event.wait(0.05):
                    return
                continue
            buf += chunk
            buf = self._drain_frames(buf)

    def _drain_frames(self, buf: bytes) -> bytes:
        """Walk through `--boundary` multipart segments.

        Each segment has the shape `<headers>\r\n\r\n<body>` between
        two boundary markers. We classify each by Content-Type:

          * application/json  → an event; buffer it as `pending_event`
          * image/jpeg        → an alarm picture; attach to the
                                pending JSON event and dispatch

        If a new JSON event arrives while one is still pending (no
        picture followed), we dispatch the previous one without a
        picture and continue. State carries across drain calls so
        partial multipart segments on a chunk boundary don't drop
        events.
        """
        import json
        while True:
            b1 = buf.find(b"--boundary")
            if b1 == -1:
                return buf
            sep = buf.find(b"\r\n\r\n", b1)
            if sep == -1:
                # Headers not fully arrived yet.
                return buf
            b2 = buf.find(b"--boundary", sep + 4)
            if b2 == -1:
                # Body still streaming in.
                return buf
            headers_blob = buf[b1:sep]
            body = buf[sep + 4 : b2]
            # Hub adds a trailing CRLF before the next boundary.
            if body.endswith(b"\r\n"):
                body = body[:-2]
            buf = buf[b2:]
            if not body:
                continue
            ctype = _parse_content_type(headers_blob)
            if "json" in ctype:
                try:
                    data = json.loads(body)
                except Exception:
                    logger.debug(
                        "AxProAlertStream: unparseable JSON part: %r", body[:120],
                    )
                    continue
                # New JSON event — flush any pending one that never got
                # its picture.
                if self._pending_event is not None:
                    self._consume_event(self._pending_event, picture=None)
                self._pending_event = data
            elif "image" in ctype:
                if self._pending_event is None:
                    logger.debug(
                        "AxProAlertStream: orphan image part (%d bytes) — no preceding event",
                        len(body),
                    )
                    continue
                self._consume_event(self._pending_event, picture=bytes(body))
                self._pending_event = None
            else:
                logger.debug(
                    "AxProAlertStream: ignoring part with content-type=%r (%d bytes)",
                    ctype, len(body),
                )

    def _consume_event(
        self, data: dict[str, Any], *, picture: Optional[bytes],
    ) -> None:
        self._events_received += 1
        for listener in self._raw_listeners:
            try:
                listener(data)
            except Exception:
                logger.exception("raw alertStream listener crashed")
        self._handle_event(data, picture=picture)

    # ─── event mapping ────────────────────────────────────────────

    def _handle_event(
        self, data: dict[str, Any], *, picture: Optional[bytes] = None,
    ) -> None:
        event_type = str(data.get("eventType") or "")
        event_state = str(data.get("eventState") or "")

        if event_type == "cidEvent":
            self._handle_cid(data, event_state)
            return
        if event_type == "zoneEvent":
            self._handle_zone(data, event_state, picture=picture)
            return
        # Everything else (videoloss, motion on a camera channel, etc.)
        # is logged at debug but not dispatched — these are typically
        # camera-side events that other parts of the pipeline own.
        logger.debug("AxProAlertStream: ignoring %s/%s", event_type, event_state)

    def _handle_cid(self, data: dict[str, Any], event_state: str) -> None:
        cid = data.get("CIDEvent") or {}
        try:
            code = int(cid.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        # Only fire on `active` — the hub sends matching `inactive`
        # frames when the condition clears. We don't dispatch on
        # `inactive` because the existing SensorEvent shape is alarm-
        # oriented.
        if event_state != "active":
            return
        # User-initiated arm/disarm events don't need to reach the
        # dispatcher — they're operator actions. Log at INFO so they
        # show up in audit but don't open incidents.
        if 1400 <= code <= 1499 or code in (1627, 1628):
            logger.info(
                "AxProAlertStream: CID %d (%s) by %r",
                code, _CID_CODE_MAP.get(code, "armDisarm"),
                cid.get("userName") or cid.get("name") or "?",
            )
            return
        mapped = _CID_CODE_MAP.get(code, f"cid_{code}")
        zone_id = _coerce_zone_id(cid.get("zone") or cid.get("zoneNo") or data.get("zoneNo"))
        event = SensorEvent(
            zone_id=zone_id if zone_id is not None else -1,
            event_type=mapped,
            event_state="alarm",
            zone_name=str(cid.get("name") or ""),
            source="axpro",
            raw=data,
        )
        logger.warning(
            "AxProAlertStream: CID %d (%s) zone=%s — dispatching",
            code, mapped, zone_id,
        )
        try:
            self._dispatcher.dispatch(event)
        except Exception:
            logger.exception("AxProAlertStream: dispatch crashed (cid=%d)", code)

    def _handle_zone(
        self, data: dict[str, Any], event_state: str,
        *, picture: Optional[bytes] = None,
    ) -> None:
        if event_state != "active":
            return
        zone = data.get("Zone") or {}
        zone_id = _coerce_zone_id(zone.get("id") or data.get("zoneNo"))
        if zone_id is None:
            return
        detector = str(zone.get("detectorType") or "")
        from itips.sensors.axpro_listener import _DETECTOR_TYPE_MAP
        event_type = _DETECTOR_TYPE_MAP.get(detector, detector or "zoneEvent")
        event = SensorEvent(
            zone_id=zone_id,
            event_type=event_type,
            event_state="alarm",
            zone_name=str(zone.get("name") or ""),
            source="axpro",
            raw=data,
            picture_bytes=picture,
        )
        # PIR-cam devices ride a JPEG along with the alarm. Surface
        # presence/absence at INFO so the operator can tell whether
        # their device has "alarm picture upload" enabled — silent
        # failure (no picture, no log) is the worst outcome.
        if detector == "pircam":
            if picture is not None:
                logger.info(
                    "AxProAlertStream: pircam zone=%d carried %d-byte JPEG",
                    zone_id, len(picture),
                )
            else:
                logger.info(
                    "AxProAlertStream: pircam zone=%d fired without a JPEG "
                    "(check 'Alarm picture upload' on the device)",
                    zone_id,
                )
        logger.warning(
            "AxProAlertStream: zoneEvent zone=%d type=%s%s — dispatching",
            zone_id, event_type,
            f" + {len(picture)}B JPEG" if picture else "",
        )
        try:
            self._dispatcher.dispatch(event)
        except Exception:
            logger.exception(
                "AxProAlertStream: dispatch crashed (zone=%d)", zone_id,
            )


def _coerce_zone_id(raw) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


_CT_RE = re.compile(rb"content-type\s*:\s*([^\r\n;]+)", re.IGNORECASE)


def _parse_content_type(headers_blob: bytes) -> str:
    """Pull the Content-Type value (lowercased, no params) from a
    multipart part's header block. Returns '' if absent."""
    m = _CT_RE.search(headers_blob)
    if not m:
        return ""
    return m.group(1).strip().lower().decode("ascii", errors="replace")
