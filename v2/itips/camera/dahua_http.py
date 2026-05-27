"""Shared HTTP primitives for talking to a Dahua IP camera.

Everything in `itips/camera/dahua_*.py` and `itips/sensors/dahua_events.py`
runs through one `DahuaCameraEndpoint` instance per camera so we have a
single place that owns credentials, base URL, digest auth, and timeouts.

There is exactly one credential set per camera. We parse it out of the
existing `ITIPS_CAMERA_<N>_RTSP` env var so operators keep a single
configuration knob and we never duplicate secrets across env keys.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import unquote, urlsplit

import requests
from requests.auth import HTTPDigestAuth

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DahuaCameraEndpoint:
    """Connection details for a Dahua camera's HTTP API."""

    host: str
    port: int
    user: str
    password: str

    # ─── construction ─────────────────────────────────────────────────

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

    # ─── primitives ───────────────────────────────────────────────────

    def auth(self) -> HTTPDigestAuth:
        return HTTPDigestAuth(self.user, self.password)

    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def cgi(self, path: str) -> str:
        """Return absolute URL for a `/cgi-bin/...` path."""
        if not path.startswith("/"):
            path = "/" + path
        return self.base() + path

    def safe_label(self) -> str:
        """User-visible identifier with credentials redacted."""
        return f"{self.host}:{self.port}"

    # ─── high-level helpers ───────────────────────────────────────────

    def get(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 5.0,
        stream: bool = False,
    ) -> requests.Response:
        """GET against `/cgi-bin/...`. Caller handles status + parsing."""
        return requests.get(
            self.cgi(path),
            auth=self.auth(),
            params=params,
            timeout=timeout,
            stream=stream,
        )

    def post(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        json_body: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> requests.Response:
        return requests.post(
            self.cgi(path),
            auth=self.auth(),
            params=params,
            data=data,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )

    def snapshot(self, channel: int = 1, timeout: float = 5.0) -> Optional["np.ndarray"]:
        """Fetch a single JPEG via `/cgi-bin/snapshot.cgi`, return BGR ndarray.

        Returns None on any failure — callers handle by logging and skipping
        the event, never by crashing the worker thread.
        """
        import cv2  # local import — keeps this module importable without cv2
        import numpy as np

        try:
            r = self.get("/cgi-bin/snapshot.cgi", params={"channel": channel}, timeout=timeout)
        except requests.RequestException as exc:
            logger.warning("snapshot %s failed: %s", self.host, exc)
            return None
        if r.status_code != 200 or not r.content:
            logger.warning(
                "snapshot %s returned HTTP %s, %d bytes",
                self.host, r.status_code, len(r.content),
            )
            return None
        # Trim anything after the JPEG end-of-image marker (FF D9).
        # Dahua snapshots routinely carry vendor metadata appended
        # after EOI; libjpeg-turbo logs noisy
        # `Corrupt JPEG data: N extraneous bytes before marker 0xfe`
        # warnings to stderr when it sees them. Truncating at EOI
        # eliminates the warning at the source without changing the
        # decoded image.
        content = r.content
        eoi = content.rfind(b"\xff\xd9")
        if eoi != -1:
            content = content[: eoi + 2]
        buf = np.frombuffer(content, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("snapshot %s: cv2.imdecode returned None", self.host)
        return frame


def endpoints_from_settings() -> dict[int, DahuaCameraEndpoint]:
    """Build an endpoint per active camera using the RTSP URLs in settings."""
    from config.settings import settings

    out: dict[int, DahuaCameraEndpoint] = {}
    for cam_id, rtsp_url in settings.cameras.active().items():
        endpoint = DahuaCameraEndpoint.from_rtsp_url(rtsp_url)
        if endpoint is not None:
            out[cam_id] = endpoint
    return out
