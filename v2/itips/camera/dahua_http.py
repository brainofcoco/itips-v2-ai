"""HTTP client utilities for Dahua IP cameras.

V2 currently decodes RTSP 24/7 to do AI on every frame, which doesn't fit on
the 8 GB Orin Nano. This module backs a cheaper, event-driven path:

  * `DahuaCameraEndpoint` — host/user/pass parsed from an existing RTSP URL,
    so the user keeps the single `ITIPS_CAMERA_<N>_RTSP` knob in `.env`.
  * `snapshot()` — single HTTP GET against `/cgi-bin/snapshot.cgi`, returns a
    decoded BGR frame (or raises). ~200-400 KB JPEG, ~50 ms on a Jetson LAN.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlsplit

import numpy as np
import requests
from requests.auth import HTTPDigestAuth

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DahuaCameraEndpoint:
    """Connection details for a Dahua camera's HTTP API."""

    host: str
    port: int
    user: str
    password: str

    @classmethod
    def from_rtsp_url(cls, rtsp_url: str, http_port: int = 80) -> Optional["DahuaCameraEndpoint"]:
        """Parse `rtsp://user:pass@host:554/...` into HTTP endpoint info.

        Returns None for empty/malformed URLs so callers can skip cameras
        that aren't configured at this site.
        """
        if not rtsp_url:
            return None
        try:
            parts = urlsplit(rtsp_url)
        except ValueError:
            return None
        host = parts.hostname
        if not host:
            return None
        user = unquote(parts.username or "")
        password = unquote(parts.password or "")
        return cls(host=host, port=http_port, user=user, password=password)

    def _auth(self) -> HTTPDigestAuth:
        return HTTPDigestAuth(self.user, self.password)

    def _base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def snapshot(self, channel: int = 1, timeout: float = 5.0) -> Optional[np.ndarray]:
        """Fetch a single JPEG via `/cgi-bin/snapshot.cgi`, return BGR ndarray.

        Returns None on any failure — callers handle by logging and skipping
        the event, never by crashing the worker thread.
        """
        import cv2  # local import — keeps this module importable in tests w/o cv2

        url = f"{self._base()}/cgi-bin/snapshot.cgi?channel={channel}"
        try:
            r = requests.get(url, auth=self._auth(), timeout=timeout)
        except requests.RequestException as exc:
            logger.warning("snapshot %s failed: %s", self.host, exc)
            return None
        if r.status_code != 200 or not r.content:
            logger.warning("snapshot %s returned HTTP %s, %d bytes",
                           self.host, r.status_code, len(r.content))
            return None
        buf = np.frombuffer(r.content, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("snapshot %s: cv2.imdecode returned None (corrupt JPEG)", self.host)
        return frame

    def safe_label(self) -> str:
        """User-visible identifier with credentials redacted."""
        return f"{self.host}:{self.port}"
