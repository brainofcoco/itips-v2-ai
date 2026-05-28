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


class PtzCommandError(RuntimeError):
    """Camera returned 200 with an error body, or non-200 status."""


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

    def save_current_as_preset(
        self, name: str, *, index: Optional[int] = None,
    ) -> "PresetInfo":
        """Write the camera's *current* PTZ position as a named preset.

        Used by the calibration wizard: operator jogs to a sensor's
        physical location, then this freezes that pose into a preset
        the dispatcher can recall on every future activation.

        Naming sequence:
          1. Pick an index — caller's choice, or the next free slot in
             [1..255] derived from `list_presets()`.
          2. Save the position via `code=SetPreset` (this is the
             operation Dahua's web UI emits when you click "Save").
          3. Set the human-readable name via `configManager.cgi`.
             Naming is a *separate* call — `SetPreset` only writes the
             pose, never the label. If naming fails we still return the
             saved index so the binding can use it numerically.
        """
        if index is None:
            used = {p.index for p in self.list_presets()}
            index = next((i for i in range(1, 256) if i not in used), None)
            if index is None:
                raise PtzCommandError(
                    "no free preset slots (255 used) — delete an old preset first"
                )
        index = int(index)
        # 1. Freeze the position.
        self._call({
            "action": "start",
            "code":   "SetPreset",
            "channel": self._channel,
            "arg1": 0,
            "arg2": index,
            "arg3": 0,
        })
        # 2. Best-effort name. Some firmwares accept the configManager
        # path, some only accept the ptz.cgi SetPresetName command,
        # and some return "OK" without actually persisting either —
        # which is why this helper *verifies by readback* instead of
        # trusting the camera's HTTP response.
        clean = (name or f"preset_{index}").strip()[:31]
        name_ok = self._try_set_preset_name(index, clean)
        if not name_ok:
            logger.warning(
                "PTZ %s: preset %d saved but name '%s' did not stick — "
                "tried all known firmware variants; the camera is keeping "
                "its auto-name. Rename via the camera's web UI if needed.",
                self._endpoint.safe_label(), index, clean,
            )
        # 3. Echo back what the camera says it has, so the caller binds
        # against the truth rather than what we asked for.
        for preset in self.list_presets():
            if preset.index == index:
                return preset
        # Fallback — the camera doesn't list our new preset yet (some
        # firmwares lag on getPresets). Synthesize a record.
        return PresetInfo(index=index, name=clean)

    def _try_set_preset_name(self, index: int, name: str) -> bool:
        """Try every known firmware variant and verify each by readback.

        The HTTP body is unreliable — some Dahua firmwares answer
        "OK" to a malformed configManager key without applying it.
        The only way to know is to refetch list_presets and compare
        the name we got back to the one we asked for.
        """
        variants = [
            # Modern firmwares: PtzPreset[] config table.
            ("PtzPreset", lambda: self._endpoint.get(
                "/cgi-bin/configManager.cgi",
                params={
                    "action": "setConfig",
                    f"PtzPreset[{index - 1}].Name":     name,
                    f"PtzPreset[{index - 1}].PresetID": str(index),
                },
                timeout=self._timeout,
            )),
            # Older NVR/IPC firmwares: PresetTitle[ch].Name[idx] table.
            ("PresetTitle", lambda: self._endpoint.get(
                "/cgi-bin/configManager.cgi",
                params={
                    "action": "setConfig",
                    f"PresetTitle[{self._channel - 1}].Name[{index - 1}]": name,
                },
                timeout=self._timeout,
            )),
            # PTZ-cam firmwares that ignore configManager but expose a
            # ptz.cgi SetPresetName command. Name goes in a query param;
            # the arg2/arg3 slots are unused for this opcode.
            ("ptz.SetPresetName", lambda: self._endpoint.get(
                "/cgi-bin/ptz.cgi",
                params={
                    "action": "start",
                    "code":   "SetPresetName",
                    "channel": self._channel,
                    "arg1": index,
                    "arg2": 0,
                    "arg3": 0,
                    "name": name,
                },
                timeout=self._timeout,
            )),
        ]
        for label, call in variants:
            try:
                r = call()
            except Exception:
                logger.exception(
                    "PTZ %s: preset-name attempt %s crashed",
                    self._endpoint.safe_label(), label,
                )
                continue
            if r.status_code != 200:
                logger.debug(
                    "PTZ %s: %s preset-name returned HTTP %d",
                    self._endpoint.safe_label(), label, r.status_code,
                )
                continue
            # Verify — the only source of truth is what the camera lists.
            if self._verify_preset_name(index, name):
                logger.info(
                    "PTZ %s: preset %d named '%s' via %s",
                    self._endpoint.safe_label(), index, name, label,
                )
                return True
            logger.debug(
                "PTZ %s: %s returned OK but name did not persist",
                self._endpoint.safe_label(), label,
            )
        return False

    def _verify_preset_name(self, index: int, expected: str) -> bool:
        for p in self.list_presets():
            if p.index == index:
                return (p.name or "").strip() == expected.strip()
        return False

    def delete_preset(self, index: int) -> None:
        """Drop a preset by index. Used by the wizard when an operator
        rebinds a zone to a different position — we clean up the old
        preset so the camera's preset table doesn't grow forever."""
        self._call({
            "action": "start",
            "code":   "ClearPreset",
            "channel": self._channel,
            "arg1": 0,
            "arg2": int(index),
            "arg3": 0,
        })

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
        """Send a ptz.cgi command and raise if the camera rejects it.

        Dahua frequently returns HTTP 200 with a body like
        `Error: Unknown PTZ Code` or `Error: Invalid Channel` when a
        command lands on a channel that doesn't have a movable lens
        (common on multi-sensor panoramic + PTZ cameras where only
        channel 2 is steerable). Status-only checks miss this entirely
        — the test console gets a green 200, the camera never moves,
        and there's nothing in the logs to explain why. So we inspect
        the body too and surface the camera-side message.
        """
        r = self._endpoint.get(
            "/cgi-bin/ptz.cgi", params=params, timeout=self._timeout,
        )
        body = (r.text or "").strip()
        if r.status_code != 200:
            msg = f"HTTP {r.status_code} {body[:120]!r}"
            logger.warning("PTZ %s: %s rejected — %s",
                           self._endpoint.safe_label(), params, msg)
            raise PtzCommandError(msg)
        # Camera-side rejection. Dahua's success body is "OK" (sometimes
        # with trailing whitespace); anything containing "Error" is a
        # rejection wrapped in a 200.
        if "error" in body.lower():
            msg = body[:160] or "camera returned non-OK body"
            logger.warning("PTZ %s: %s rejected by camera — %s",
                           self._endpoint.safe_label(), params, msg)
            raise PtzCommandError(msg)

    def _probe(self) -> None:
        """Probe channel 1, fall back to channel 2 for multi-sensor models.

        On dual-sensor (panoramic + PTZ) Dahua cameras, channel 1 is
        usually the fixed panoramic lens and channel 2 is the steerable
        head. `getPresets` against the wrong channel returns 200 OK
        with body `Error: Bad Channel` (or similar) — so we sniff the
        body, not just the status.
        """
        candidates = (self._channel,) if self._channel != 1 else (1, 2)
        chosen, last_body = None, ""
        for ch in candidates:
            try:
                r = self._endpoint.get(
                    "/cgi-bin/ptz.cgi",
                    params={"action": "getPresets", "channel": ch},
                    timeout=2.0,
                )
            except Exception:
                continue
            body = (r.text or "").strip()
            last_body = body
            if r.status_code == 200 and "error" not in body.lower():
                chosen = ch
                break
        if chosen is None:
            self._connected = False
            logger.info(
                "PTZ cam %d: no responsive channel — last body=%r",
                self.camera_id, last_body[:120],
            )
            return
        if chosen != self._channel:
            logger.info(
                "PTZ cam %d: channel %d had no PTZ — using channel %d instead",
                self.camera_id, self._channel, chosen,
            )
            self._channel = chosen
        self._connected = True
        logger.info(
            "PTZ cam %d ready at %s (channel=%d)",
            self.camera_id, self._endpoint.safe_label(), self._channel,
        )


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
        # Optional pin for multi-sensor models where auto-detect can't
        # reach channel 2 (firmware quirk, ONVIF permissions, etc.).
        ch_raw = os.getenv(f"ITIPS_PTZ_{cam_id}_CHANNEL")
        try:
            channel = int(ch_raw) if ch_raw else 1
        except ValueError:
            logger.warning(
                "PTZ cam %d: bad ITIPS_PTZ_%d_CHANNEL=%r, falling back to 1",
                cam_id, cam_id, ch_raw,
            )
            channel = 1
        out[cam_id] = DahuaPTZ(endpoint, camera_id=cam_id, channel=channel)
    return out


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
