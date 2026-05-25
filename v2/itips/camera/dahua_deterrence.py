"""TiOC strobe + speaker control for Dahua cameras.

Most ITIPS deterrence fires from the camera autonomously (REQ-AI-23: the
camera's own AI lights the strobe and triggers the speaker within 500ms
of confirming a human). This module covers the two cases the Jetson does
need to drive:

* `fire()`     — manual trigger from the operator dashboard.
* `standdown()` — temporarily silence the strobe/speaker during a
                  maintenance window (matches the existing B4 command).

Reference: Dahua HTTP API V3.98, `coaxialControlIO.cgi`.

Hardware gate
-------------
Only TiOC cameras (e.g. `SD49425GB-HNR`, `DH-SDT4E425-4F-GB-A-PV1-S2`)
have the white-light strobe and 110dB speaker wired through the coaxial
control bus. Calling these methods on a non-TiOC camera (e.g. the 8MP
`DH-SD8C848PA-HNF`) returns an HTTP error from the camera and we log +
move on — the AlertEngine drives an external horn on those sites instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from itips.camera.dahua_http import DahuaCameraEndpoint

logger = logging.getLogger(__name__)


# coaxialControlIO `Type` constants
_TYPE_WHITE_LIGHT = 1
_TYPE_SPEAKER = 2
# coaxialControlIO `IO` constants
_IO_ON = 1
_IO_OFF = 2
# coaxialControlIO `TriggerMode` constants
_MODE_LINKED = 1   # camera's own AI gates the device
_MODE_MANUAL = 2   # API explicitly drives it


@dataclass(frozen=True)
class DeterrenceStatus:
    white_light_on: bool
    speaker_on: bool


class DahuaDeterrence:
    """One camera's strobe + speaker."""

    def __init__(self, endpoint: DahuaCameraEndpoint, *, channel: int = 1, timeout: float = 5.0) -> None:
        self._endpoint = endpoint
        self._channel = channel
        self._timeout = timeout

    # ─── status ───────────────────────────────────────────────────────

    def status(self) -> DeterrenceStatus:
        r = self._endpoint.get(
            "/cgi-bin/coaxialControlIO.cgi",
            params={"action": "getstatus", "channel": self._channel},
            timeout=self._timeout,
        )
        r.raise_for_status()
        white = "status.whitelight=on" in r.text.lower()
        speaker = "status.speaker=on" in r.text.lower()
        return DeterrenceStatus(white_light_on=white, speaker_on=speaker)

    # ─── controls ─────────────────────────────────────────────────────

    def fire(self, *, light: bool = True, speaker: bool = True) -> None:
        """Manually engage the strobe and/or speaker."""
        if light:
            self._control(_TYPE_WHITE_LIGHT, _IO_ON, _MODE_MANUAL)
        if speaker:
            self._control(_TYPE_SPEAKER, _IO_ON, _MODE_MANUAL)
        logger.info(
            "Deterrence FIRED on %s (light=%s, speaker=%s)",
            self._endpoint.safe_label(), light, speaker,
        )

    def standdown(self) -> None:
        """Silence both devices and hand control back to the camera AI."""
        self._control(_TYPE_WHITE_LIGHT, _IO_OFF, _MODE_LINKED)
        self._control(_TYPE_SPEAKER, _IO_OFF, _MODE_LINKED)
        logger.info("Deterrence STAND-DOWN on %s", self._endpoint.safe_label())

    # ─── internals ────────────────────────────────────────────────────

    def _control(self, type_: int, io: int, mode: int) -> None:
        r = self._endpoint.get(
            "/cgi-bin/coaxialControlIO.cgi",
            params={
                "action": "control",
                "channel": self._channel,
                "info[0].Type": type_,
                "info[0].IO": io,
                "info[0].TriggerMode": mode,
            },
            timeout=self._timeout,
        )
        # Some cameras return 200 with body "Error" for unsupported devices.
        if r.status_code != 200 or "error" in r.text.lower():
            logger.warning(
                "coaxialControlIO %s rejected type=%d io=%d mode=%d: HTTP %d %r",
                self._endpoint.safe_label(), type_, io, mode, r.status_code, r.text[:80],
            )
