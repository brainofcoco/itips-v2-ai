"""Per-camera capability routing for the ML fallback layer.

Condenses the dahua_health probe matrix into yes/no decisions for
event handlers: "does this camera need the Jetson fallback for X?"
Operator overrides take precedence over probe results.
"""

from __future__ import annotations

import enum
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Capability(str, enum.Enum):
    FACE_RECOGNITION = "face_recognition"
    ANPR = "anpr"
    IVS_RULES = "ivs_rules"
    DETERRENCE = "deterrence"
    SNAPSHOT = "snapshot"
    LOCAL_STORAGE = "local_storage"


# A capability is "native" only if ALL its probes (named per
# dahua_health.py) report STATUS_OK.
_NATIVE_PROBES: dict[Capability, tuple[str, ...]] = {
    Capability.FACE_RECOGNITION: ("face_recognition_db", "face_group_channel"),
    Capability.ANPR: ("anpr_redlist", "anpr_event_attach"),
    Capability.IVS_RULES: ("ivs_rule_types",),
    Capability.DETERRENCE: ("deterrence",),
    Capability.SNAPSHOT: ("snapshot",),
    Capability.LOCAL_STORAGE: ("sd_storage",),
}


@dataclass
class CapabilitySnapshot:
    camera_id: int
    native: dict[Capability, bool] = field(default_factory=dict)
    details: dict[Capability, str] = field(default_factory=dict)

    def needs_fallback(self, cap: Capability) -> bool:
        return not self.native.get(cap, False)


class CapabilityRouter:
    """Per-camera capability decisions, override-aware.

    Two inputs: probe results (refreshed by `update_from_health`) and
    operator overrides (persisted to `overrides_path`). Overrides win.
    """

    def __init__(self, overrides_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._by_camera: dict[int, CapabilitySnapshot] = {}
        # (cam, cap) → True=force fallback, False=pin native, missing=follow probe.
        self._overrides: dict[tuple[int, Capability], bool] = {}
        self._overrides_path = overrides_path
        if overrides_path is not None:
            self._load_overrides()

    def update_from_health(self, health_body: dict[str, Any]) -> None:
        """Re-derive from a `dahua_health.run_for_all` dict. Idempotent."""
        next_by_camera: dict[int, CapabilitySnapshot] = {}
        for cam in health_body.get("cameras", []):
            cam_id = int(cam.get("camera_id", -1))
            if cam_id < 0:
                continue
            checks_by_name = {c.get("name"): c for c in cam.get("checks", [])}
            snap = CapabilitySnapshot(camera_id=cam_id)
            for cap, probe_names in _NATIVE_PROBES.items():
                statuses = [
                    (checks_by_name.get(p) or {}).get("status", "missing")
                    for p in probe_names
                ]
                snap.native[cap] = all(s == "ok" for s in statuses)
                snap.details[cap] = ",".join(
                    f"{p}={s}" for p, s in zip(probe_names, statuses)
                )
            next_by_camera[cam_id] = snap

        with self._lock:
            self._by_camera = next_by_camera
        logger.info("capability router refreshed: %d camera(s)", len(next_by_camera))

    def set_camera(self, snap: CapabilitySnapshot) -> None:
        """Inject a snapshot directly — mainly for tests."""
        with self._lock:
            self._by_camera[snap.camera_id] = snap

    def needs_fallback(self, camera_id: int, cap: Capability) -> bool:
        """Override beats probe. No data either → conservatively native
        (don't spin up ML for cameras we know nothing about)."""
        with self._lock:
            forced = self._overrides.get((camera_id, cap))
        if forced is not None:
            return forced
        snap = self._snapshot(camera_id)
        if snap is None:
            return False
        return snap.needs_fallback(cap)

    def get(self, camera_id: int) -> Optional[CapabilitySnapshot]:
        return self._snapshot(camera_id)

    def summary(self) -> dict[int, dict[str, bool]]:
        """`{cam_id: {cap_name: is_effective_native}}` — overrides applied."""
        with self._lock:
            out: dict[int, dict[str, bool]] = {}
            for cid, snap in self._by_camera.items():
                row: dict[str, bool] = {}
                for cap in Capability:
                    override = self._overrides.get((cid, cap))
                    if override is not None:
                        row[cap.value] = not override
                    else:
                        row[cap.value] = snap.native.get(cap, False)
                out[cid] = row
            return out

    def set_override(self, camera_id: int, cap: Capability,
                     force_fallback: Optional[bool]) -> None:
        """True=force fallback, False=pin native, None=follow probe."""
        key = (camera_id, cap)
        with self._lock:
            if force_fallback is None:
                self._overrides.pop(key, None)
            else:
                self._overrides[key] = bool(force_fallback)
            self._save_overrides_locked()
        logger.info(
            "capability override: cam %d %s → %s",
            camera_id, cap.value,
            "follow probe" if force_fallback is None
            else ("FORCE FALLBACK" if force_fallback else "PIN NATIVE"),
        )

    def overrides(self) -> dict[int, dict[str, bool]]:
        """`{cam_id: {cap_name: True=force_fallback}}` for the dashboard."""
        with self._lock:
            out: dict[int, dict[str, bool]] = {}
            for (cid, cap), forced in self._overrides.items():
                out.setdefault(cid, {})[cap.value] = forced
            return out

    def _load_overrides(self) -> None:
        assert self._overrides_path is not None
        if not self._overrides_path.exists():
            return
        try:
            raw = json.loads(self._overrides_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("capability overrides at %s unreadable: %s",
                           self._overrides_path, exc)
            return
        with self._lock:
            for cam_key, by_cap in (raw or {}).items():
                try:
                    cam_id = int(cam_key)
                except (ValueError, TypeError):
                    continue
                if not isinstance(by_cap, dict):
                    continue
                for cap_name, forced in by_cap.items():
                    try:
                        cap = Capability(cap_name)
                    except ValueError:
                        continue
                    self._overrides[(cam_id, cap)] = bool(forced)
        if self._overrides:
            logger.info("loaded %d capability override(s) from %s",
                        len(self._overrides), self._overrides_path)

    def _save_overrides_locked(self) -> None:
        if self._overrides_path is None:
            return
        payload: dict[str, dict[str, bool]] = {}
        for (cid, cap), forced in self._overrides.items():
            payload.setdefault(str(cid), {})[cap.value] = forced
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._overrides_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._overrides_path)
        except OSError:
            logger.exception("could not persist capability overrides to %s",
                             self._overrides_path)

    def _snapshot(self, camera_id: int) -> Optional[CapabilitySnapshot]:
        with self._lock:
            return self._by_camera.get(camera_id)
