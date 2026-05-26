"""Pan/Tilt/Zoom control via the camera's native ptz.cgi endpoint.

Replaces the ONVIF-based stub in `itips/alerts/ptz.py`. The HTTP surface
is simpler than ONVIF, runs on the same digest auth the rest of the
Dahua API uses, and gives us access to the camera's own preset table —
no need for a Jetson-side preset registry.

Reference: Dahua HTTP API V3.98, `ptz.cgi` / `ptzBase.cgi`.

Coordinate convention
---------------------
`move_to_box_center(bbox, frame_size)` accepts a pixel-space bounding box
from a Dahua event payload (which uses the 0–8192 normalized space) and
issues `ptzBase.cgi?action=moveDirectly` with the centre as the target
endPoint. The camera does the geometry.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from itips.camera.dahua_http import DahuaCameraEndpoint, endpoints_from_settings

logger = logging.getLogger(__name__)


_DAHUA_COORD_MAX = 8192


@dataclass(frozen=True)
class PresetInfo:
    index: int
    name: str
    pan: Optional[float] = None
    tilt: Optional[float] = None
    zoom: Optional[float] = None


class DahuaPTZ:
    """Per-camera PTZ wrapper. One instance per camera_id."""

    def __init__(
        self,
        endpoint: DahuaCameraEndpoint,
        *,
        camera_id: int,
        channel: int = 1,
        timeout: float = 5.0,
    ) -> None:
        self._endpoint = endpoint
        self.camera_id = camera_id
        self._channel = channel
        self._timeout = timeout
        self._connected = False
        self._probe()

    # ─── state ────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ─── controls ─────────────────────────────────────────────────────

    def go_home(self) -> None:
        try:
            self._call({"action": "start", "code": "GotoPreset", "arg1": 0, "arg2": 1, "arg3": 0})
        except Exception:
            logger.exception("PTZ %s: go_home failed", self._endpoint.safe_label())

    def go_to_preset(self, preset_index: int) -> None:
        self._call({
            "action": "start",
            "code": "GotoPreset",
            "channel": self._channel,
            "arg1": 0,
            "arg2": int(preset_index),
            "arg3": 0,
        })

    def goto_preset_by_name(self, name: str) -> bool:
        """Names survive a preset-renumber and read better in the UI than indices."""
        if not name:
            return False
        target = name.strip().lower()
        for preset in self.list_presets():
            if (preset.name or "").strip().lower() == target:
                self.go_to_preset(preset.index)
                return True
        logger.info(
            "PTZ cam %d: no preset matching name=%r — known names=%s",
            self.camera_id, name,
            ", ".join(p.name for p in self.list_presets()) or "(none)",
        )
        return False

    def list_presets(self) -> list[PresetInfo]:
        from re import compile as re_compile

        r = self._endpoint.get(
            "/cgi-bin/ptz.cgi",
            params={"action": "getPresets", "channel": self._channel},
            timeout=self._timeout,
        )
        if r.status_code != 200:
            return []
        rows: dict[int, dict] = {}
        pat = re_compile(r"presets\[(\d+)\]\.([A-Za-z]+)(?:\[(\d+)\])?=(.*)")
        for line in r.text.splitlines():
            m = pat.match(line.strip())
            if not m:
                continue
            idx = int(m.group(1))
            field = m.group(2)
            sub = m.group(3)
            value = m.group(4).strip()
            rows.setdefault(idx, {})
            if sub is None:
                rows[idx][field] = value
            else:
                rows[idx].setdefault(field, {})[int(sub)] = value
        out: list[PresetInfo] = []
        for idx in sorted(rows.keys()):
            row = rows[idx]
            pos = row.get("Position", {})
            try:
                rec_index = int(row.get("Index", idx))
            except ValueError:
                rec_index = idx
            out.append(PresetInfo(
                index=rec_index,
                name=row.get("Name", f"preset-{rec_index}"),
                pan=_safe_float(pos.get(0)),
                tilt=_safe_float(pos.get(1)),
                zoom=_safe_float(pos.get(2)),
            ))
        return out

    def move_directly(
        self,
        *,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> None:
        """Move so that `end` is at frame centre. Coords are 0–8192."""
        self._endpoint.get(
            "/cgi-bin/ptzBase.cgi",
            params={
                "action": "moveDirectly",
                "channel": self._channel,
                "startPoint[0]": int(start[0]),
                "startPoint[1]": int(start[1]),
                "endPoint[0]": int(end[0]),
                "endPoint[1]": int(end[1]),
            },
            timeout=self._timeout,
        )

    def move_to_box_centre(
        self,
        bbox: tuple[float, float, float, float],
        *,
        frame_width: int,
        frame_height: int,
    ) -> None:
        """Centre on the midpoint of a bounding box.

        The bbox is expected in pixel space matching `frame_width x frame_height`.
        Dahua event payloads use a 0–8192 normalised space already; pass the
        right `frame_width`/`frame_height` and we'll re-normalise here.
        """
        if frame_width <= 0 or frame_height <= 0:
            return
        x1, y1, x2, y2 = bbox
        cx = int(((x1 + x2) / 2.0) / frame_width * _DAHUA_COORD_MAX)
        cy = int(((y1 + y2) / 2.0) / frame_height * _DAHUA_COORD_MAX)
        self.move_directly(
            start=(_DAHUA_COORD_MAX // 2, _DAHUA_COORD_MAX // 2),
            end=(cx, cy),
        )

    # ─── jog control ──────────────────────────────────────────────────

    JOG_CODES = {
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "left_up": "LeftUp", "right_up": "RightUp",
        "left_down": "LeftDown", "right_down": "RightDown",
        "zoom_wide": "ZoomWide", "zoom_tele": "ZoomTele",
        "focus_near": "FocusNear", "focus_far": "FocusFar",
    }

    def jog_start(self, direction: str, *, speed: int = 4) -> None:
        """Begin a continuous move. Must be paired with jog_stop()."""
        code = self.JOG_CODES.get(direction)
        if code is None:
            raise ValueError(f"unknown jog direction {direction!r}")
        speed = max(1, min(8, int(speed)))
        self._call({
            "action": "start", "code": code, "channel": self._channel,
            "arg1": 0, "arg2": speed, "arg3": 0,
        })

    def jog_stop(self, direction: str) -> None:
        code = self.JOG_CODES.get(direction)
        if code is None:
            raise ValueError(f"unknown jog direction {direction!r}")
        self._call({
            "action": "stop", "code": code, "channel": self._channel,
            "arg1": 0, "arg2": 0, "arg3": 0,
        })

    def apply_override(self, params: dict) -> None:
        """B4 backend command. Accepts {preset, pan, tilt, zoom, bbox, ...}."""
        if "preset_id" in params:
            try:
                self.go_to_preset(int(params["preset_id"]))
                return
            except (TypeError, ValueError):
                logger.warning("PTZ override: bad preset_id %r", params["preset_id"])
        if "bbox" in params and "frame_width" in params and "frame_height" in params:
            self.move_to_box_centre(
                tuple(params["bbox"]),
                frame_width=int(params["frame_width"]),
                frame_height=int(params["frame_height"]),
            )
            return
        if "pan" in params or "tilt" in params:
            pan = float(params.get("pan", 0.0))
            tilt = float(params.get("tilt", 0.0))
            zoom = float(params.get("zoom", 0.0))
            self._call({
                "action": "start",
                "code": "PositionABS",
                "channel": self._channel,
                "arg1": pan,
                "arg2": tilt,
                "arg3": zoom,
            })

    # ─── internals ────────────────────────────────────────────────────

    def _call(self, params: dict) -> None:
        r = self._endpoint.get("/cgi-bin/ptz.cgi", params=params, timeout=self._timeout)
        if r.status_code != 200:
            logger.warning(
                "PTZ %s: %s rejected (HTTP %d %r)",
                self._endpoint.safe_label(), params, r.status_code, r.text[:80],
            )

    def _probe(self) -> None:
        """Cheap connection check — list presets on a dedicated short timeout."""
        try:
            r = self._endpoint.get(
                "/cgi-bin/ptz.cgi",
                params={"action": "getPresets", "channel": self._channel},
                timeout=2.0,
            )
            self._connected = r.status_code == 200
        except Exception:
            self._connected = False
        if self._connected:
            logger.info("PTZ cam %d ready at %s", self.camera_id, self._endpoint.safe_label())


# ─── factory ──────────────────────────────────────────────────────────


def build_all() -> dict[int, DahuaPTZ]:
    """Build one DahuaPTZ per camera with `ITIPS_PTZ_<N>_ENABLED=true`.

    Falls back to building one PTZ per active camera if no explicit
    enable flags are set, since with the new arch every Dahua camera
    speaks ptz.cgi anyway.
    """
    endpoints = endpoints_from_settings()
    explicit_enabled = {
        cam_id for cam_id in endpoints
        if os.getenv(f"ITIPS_PTZ_{cam_id}_ENABLED", "").lower() == "true"
    }
    target = explicit_enabled if explicit_enabled else set(endpoints.keys())
    out: dict[int, DahuaPTZ] = {}
    for cam_id in sorted(target):
        endpoint = endpoints.get(cam_id)
        if endpoint is None:
            continue
        out[cam_id] = DahuaPTZ(endpoint, camera_id=cam_id)
    return out


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
