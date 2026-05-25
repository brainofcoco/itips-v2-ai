"""PTZ controller — ONVIF wrapper with sensor-triggered pan + zoom-capture.

This module owns local PTZ behaviour. Backend-initiated PTZ overrides
arrive via the B4 commands endpoint and call `apply_override(payload)`.

The implementation is intentionally thin in V2 — the V1 PTZ controller is
mature and battle-tested. Port the full body of V1's `ptz_control.py` in
once we have a Jetson on the bench for integration testing. Until then
this module exposes the smallest surface the rest of the V2 pipeline
needs (build_all, apply_override, go_home, is_connected, is_tracking).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class PTZController:
    """Single-camera ONVIF PTZ wrapper."""

    def __init__(self, *, camera_id: int, ip: str, port: int, username: str, password: str) -> None:
        self.camera_id = camera_id
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self._connected = False
        self._tracking = False
        self._initialize()

    @classmethod
    def build_all(cls) -> dict[int, "PTZController"]:
        """Construct one controller per ITIPS_PTZ_<N>_ENABLED=true camera."""
        controllers: dict[int, PTZController] = {}
        for cam_id in (1, 4):
            if os.getenv(f"ITIPS_PTZ_{cam_id}_ENABLED", "false").lower() != "true":
                continue
            ip = os.getenv(f"ITIPS_PTZ_{cam_id}_IP", "")
            if not ip:
                continue
            controllers[cam_id] = cls(
                camera_id=cam_id,
                ip=ip,
                port=int(os.getenv(f"ITIPS_PTZ_{cam_id}_PORT", "80")),
                username=os.getenv(f"ITIPS_PTZ_{cam_id}_USER", "admin"),
                password=os.getenv(f"ITIPS_PTZ_{cam_id}_PASS", ""),
            )
        return controllers

    # ─── state ────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_tracking(self) -> bool:
        return self._tracking

    # ─── operations ───────────────────────────────────────────────

    def apply_override(self, params: dict[str, Any]) -> None:
        """Move to the requested pan/tilt/zoom. Backend-initiated (B4)."""
        if not self._connected:
            logger.warning("PTZ cam %d: override requested but camera not connected", self.camera_id)
            return
        logger.info("PTZ cam %d override: %s", self.camera_id, params)

    def go_home(self) -> None:
        if not self._connected:
            return
        logger.info("PTZ cam %d → home", self.camera_id)
        self._tracking = False

    def stop_tracking(self) -> None:
        if self._tracking:
            self._tracking = False
            logger.info("PTZ cam %d tracking stopped", self.camera_id)

    # ─── construction ─────────────────────────────────────────────

    def _initialize(self) -> None:
        """Defer ONVIF connection until we have a Jetson with a real PTZ
        attached. Logged as connected=false so the pipeline stays usable
        in simulation.
        """
        logger.info("PTZ cam %d configured at %s:%d (connection deferred to integration)",
                    self.camera_id, self.ip, self.port)
        self._connected = False
