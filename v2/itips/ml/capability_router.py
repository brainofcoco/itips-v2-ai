"""Per-camera capability routing for the ML fallback layer.

The Dahua health check (`itips/camera/dahua_health.py`) produces a
per-camera matrix of probe results. This module condenses that matrix
into yes/no questions the event handlers care about:

    "Does cam 3 do face recognition natively, or do I run InsightFace?"
    "Does cam 1 do ANPR natively, or do I run OCR?"
    "Does cam 4 have IVS rules deployed, or do I run zone logic?"

Sits read-only on top of `dahua_health` — feed it a fresh health
snapshot whenever one runs; query it cheaply from any event handler.
"""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Capability(str, enum.Enum):
    """The set of features for which a Jetson-side fallback exists."""

    FACE_RECOGNITION = "face_recognition"
    ANPR = "anpr"
    IVS_RULES = "ivs_rules"
    DETERRENCE = "deterrence"
    SNAPSHOT = "snapshot"
    LOCAL_STORAGE = "local_storage"


# Which probe(s) decide whether a capability is "native". A capability
# is considered native only if **all** of its probes report STATUS_OK.
# Probes are referenced by the `name` field used in `dahua_health.py`.
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
    """One camera's capability vector, as seen by the router."""

    camera_id: int
    native: dict[Capability, bool] = field(default_factory=dict)
    details: dict[Capability, str] = field(default_factory=dict)

    def needs_fallback(self, cap: Capability) -> bool:
        return not self.native.get(cap, False)


class CapabilityRouter:
    """Thread-safe snapshot store of per-camera capability decisions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_camera: dict[int, CapabilitySnapshot] = {}

    # ─── feeding ──────────────────────────────────────────────────────

    def update_from_health(self, health_body: dict[str, Any]) -> None:
        """Re-derive every capability from a `run_for_all` dict.

        `health_body` is the structure returned by
        `dahua_health.run_for_all` (or its `/api/health/cameras`
        wrapping). Safe to call repeatedly — replaces in place.
        """
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
        """Inject a single camera's snapshot. Mostly for tests."""
        with self._lock:
            self._by_camera[snap.camera_id] = snap

    # ─── querying ─────────────────────────────────────────────────────

    def needs_fallback(self, camera_id: int, cap: Capability) -> bool:
        """`True` if cam doesn't natively do `cap` — run the ML fallback.

        Conservative: if we have no snapshot for this camera (health
        check hasn't run yet), assume native to avoid spinning up
        expensive ML for cameras we know nothing about.
        """
        snap = self._snapshot(camera_id)
        if snap is None:
            return False
        return snap.needs_fallback(cap)

    def get(self, camera_id: int) -> Optional[CapabilitySnapshot]:
        return self._snapshot(camera_id)

    def summary(self) -> dict[int, dict[str, bool]]:
        """For the dashboard: `{cam_id: {cap_name: is_native}}`."""
        with self._lock:
            return {
                cid: {cap.value: snap.native.get(cap, False) for cap in Capability}
                for cid, snap in self._by_camera.items()
            }

    def _snapshot(self, camera_id: int) -> Optional[CapabilitySnapshot]:
        with self._lock:
            return self._by_camera.get(camera_id)
