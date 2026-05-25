"""Long-poll listener for a single Dahua IP camera's event manager.

Subscribes to `/cgi-bin/eventManager.cgi?action=attach&codes=[...]` and
streams parsed events back through `on_event`. One thread per camera.

Dahua emits each event on its own line(s) inside a multipart-style stream:

    Code=VideoMotion;action=Start;index=0
    Code=CrossLineDetection;action=Start;index=0;data={...}

The `heartbeat` query param keeps NAT/firewalls from killing the connection
when the camera is idle. On socket drop we reconnect with exponential backoff
so a flaky camera doesn't melt the Jetson.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from requests.auth import HTTPDigestAuth

from itips.camera.dahua_http import DahuaCameraEndpoint

logger = logging.getLogger(__name__)

# Codes we actually care about. "All" works on most Dahua firmwares but spams
# the stream; this short list keeps parsing predictable.
DEFAULT_CODES = (
    "VideoMotion",
    "CrossLineDetection",
    "CrossRegionDetection",
    "ObjectDetect",
)

_EVENT_LINE_RE = re.compile(r"Code=([^;]+);action=([^;]+);(?:index=(\d+))?(.*)$")


@dataclass
class DahuaEvent:
    """A single parsed event from the camera's event manager."""

    camera_id: int
    code: str            # e.g. "VideoMotion"
    action: str          # "Start" | "Stop" | "Pulse"
    index: int           # channel/rule index
    data: dict           # parsed JSON payload (often empty)
    monotonic_ns: int    # local monotonic time for cooldown logic


def parse_event_line(line: str, camera_id: int) -> Optional[DahuaEvent]:
    """Parse one line of Dahua eventManager output."""
    m = _EVENT_LINE_RE.match(line.strip())
    if not m:
        return None
    code, action, idx, tail = m.groups()
    data: dict = {}
    if "data=" in tail:
        try:
            data = json.loads(tail.split("data=", 1)[1])
        except (ValueError, IndexError):
            data = {}
    return DahuaEvent(
        camera_id=camera_id,
        code=code.strip(),
        action=action.strip(),
        index=int(idx) if idx else 0,
        data=data,
        monotonic_ns=time.monotonic_ns(),
    )


class DahuaEventListener(threading.Thread):
    """Per-camera long-poll subscriber to `/cgi-bin/eventManager.cgi`.

    Run as a daemon — `stop()` to shut down cleanly. All exceptions are
    caught inside the run loop so a flaky camera can never crash the
    worker thread.
    """

    def __init__(self, endpoint: DahuaCameraEndpoint, camera_id: int,
                 on_event: Callable[[DahuaEvent], None],
                 codes: tuple[str, ...] = DEFAULT_CODES,
                 heartbeat_s: int = 10,
                 max_backoff_s: float = 30.0) -> None:
        super().__init__(name=f"dahua-events-{camera_id}", daemon=True)
        self._endpoint = endpoint
        self._camera_id = camera_id
        self._on_event = on_event
        self._codes = codes
        self._heartbeat_s = heartbeat_s
        self._max_backoff_s = max_backoff_s
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        codes_str = ",".join(self._codes)
        url = (f"http://{self._endpoint.host}:{self._endpoint.port}"
               f"/cgi-bin/eventManager.cgi?action=attach"
               f"&codes=[{codes_str}]&heartbeat={self._heartbeat_s}")
        auth = HTTPDigestAuth(self._endpoint.user, self._endpoint.password)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                # No total timeout: this is intentionally a long-lived stream.
                # `read=heartbeat*2` gives the camera time to send a keepalive
                # before we treat the connection as dead.
                with requests.get(url, auth=auth, stream=True,
                                  timeout=(5, self._heartbeat_s * 2)) as r:
                    if r.status_code != 200:
                        logger.warning("cam %d events: HTTP %d, retrying",
                                       self._camera_id, r.status_code)
                        self._sleep_backoff(backoff)
                        backoff = min(backoff * 2, self._max_backoff_s)
                        continue
                    backoff = 1.0
                    logger.info("cam %d event stream connected (%s)",
                                self._camera_id, self._endpoint.safe_label())
                    # Dahua does not send a charset, so requests returns bytes
                    # even with decode_unicode=True. Decode explicitly.
                    for raw in r.iter_lines():
                        if self._stop.is_set():
                            return
                        if not raw:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        if "Heartbeat" in raw:
                            continue
                        event = parse_event_line(raw, self._camera_id)
                        if event is None:
                            continue
                        try:
                            self._on_event(event)
                        except Exception:
                            logger.exception("cam %d on_event handler crashed",
                                             self._camera_id)
            except requests.RequestException as exc:
                if self._stop.is_set():
                    return
                logger.warning("cam %d event stream dropped (%s); reconnecting in %.1fs",
                               self._camera_id, exc, backoff)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._max_backoff_s)
            except Exception:
                logger.exception("cam %d event loop unexpected error",
                                 self._camera_id)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._max_backoff_s)

    def _sleep_backoff(self, seconds: float) -> None:
        # Interruptible by stop().
        self._stop.wait(timeout=seconds)
