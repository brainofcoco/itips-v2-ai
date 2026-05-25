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
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
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
    """Thread-safe snapshot store of per-camera capability decisions.

    Two layers feed `needs_fallback`:

    1. **Probe results** (the snapshot) — updated by `update_from_health`
       whenever the dashboard re-runs the Dahua capability probe. A
       missing native probe makes that capability fall back.
    2. **Operator overrides** — explicit "I don't trust this camera's
       native X, always run the Jetson model" toggles flipped from the
       dashboard. Survive restart via `overrides_path` (a small JSON
       file on the NVMe).

    Overrides take precedence over probe results both ways: an
    operator can force fallback on a camera the probe says is fine,
    or pin a camera to native if they explicitly want to.
    """

    def __init__(self, overrides_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._by_camera: dict[int, CapabilitySnapshot] = {}
        # (camera_id, Capability) → bool. True = force fallback,
        # False = pin to native, missing key = follow the probe.
        self._overrides: dict[tuple[int, Capability], bool] = {}
        self._overrides_path = overrides_path
        if overrides_path is not None:
            self._load_overrides()

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

        Override beats probe. Operator-forced fallback wins even when
        the camera reports native support; operator-pinned-native wins
        even when the probe says the feature is missing. Without
        either signal we fall back to the probe; with no probe data
        either we conservatively say "native" so we don't fire up
        ML for cameras we know nothing about.
        """
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
        """For the dashboard: `{cam_id: {cap_name: is_native}}`.

        "is_native" here means the **effective** native flag — probe
        flipped by any override the operator set. Lets the UI render
        the same matrix regardless of how a decision was reached.
        """
        with self._lock:
            out: dict[int, dict[str, bool]] = {}
            for cid, snap in self._by_camera.items():
                row: dict[str, bool] = {}
                for cap in Capability:
                    override = self._overrides.get((cid, cap))
                    if override is not None:
                        row[cap.value] = not override  # forced fallback ↔ not native
                    else:
                        row[cap.value] = snap.native.get(cap, False)
                out[cid] = row
            return out

    # ─── overrides ────────────────────────────────────────────────────

    def set_override(self, camera_id: int, cap: Capability,
                     force_fallback: Optional[bool]) -> None:
        """Pin a camera's capability decision.

        `True`  → always run the ML fallback for this (cam, cap).
        `False` → always trust the native path even if the probe says
                  the feature is missing.
        `None`  → clear the override; fall back to probe behaviour.
        """
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
        """`{cam_id: {cap_name: True_means_force_fallback}}` for the UI."""
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
