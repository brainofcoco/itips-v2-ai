"""Event-worker behavior fallback wiring.

Drives `_handle_motion` with a stub BehaviorEngine + CapabilityRouter
to confirm the dispatcher relays synthesised IVS alerts through the
same `handle_behaviour_alert_simple` path the native CrossLine /
CrossRegion / Wander handlers use.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np

from itips.ml.behavior_engine import BehaviorAlert
from itips.ml.capability_router import Capability, CapabilityRouter, CapabilitySnapshot
from itips.runtime.event_worker import DahuaEventDispatcher, WorkerDeps
from itips.sensors.dahua_events import DahuaEvent


def _motion_event() -> DahuaEvent:
    return DahuaEvent(camera_id=1, code="VideoMotion", action="Start",
                      index=0, data={})


def _make_dispatcher(behavior_engine, capability_router, alert_engine,
                     *, behavior_cooldown_s: float = 0.0):
    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=MagicMock(),
        recorders={},
        event_tap=None,
        capability_router=capability_router,
        behavior_engine=behavior_engine,
    )
    d = DahuaEventDispatcher.__new__(DahuaEventDispatcher)
    d.camera_id = 1
    d.deps = deps
    # Reset cooldowns + counters that __init__ would normally set.
    d._plate_fallback_cooldown_s = 10.0
    d._plate_fallback_last_run = 0.0
    d._behavior_fallback_cooldown_s = behavior_cooldown_s
    d._behavior_fallback_last_run = 0.0
    return d


def _frame():
    return np.zeros((480, 640, 3), dtype="uint8")


def _router_with(cap: Capability, native: bool) -> CapabilityRouter:
    r = CapabilityRouter()
    r.set_camera(CapabilitySnapshot(camera_id=1, native={cap: native}))
    return r


def _alert(alert_type: str = "intrusion") -> BehaviorAlert:
    return BehaviorAlert(
        alert_type=alert_type, zone_id="z1", zone_name="compound",
        track_id=7, class_name="person",
        bbox=(10.0, 10.0, 50.0, 100.0),
        details={"rule_name": "compound"},
    )


# ─── routing ────────────────────────────────────────────────────────


def test_native_ivs_path_does_not_call_engine():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=True)
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), _frame())
    engine.analyse.assert_not_called()
    # Bare motion log still fires.
    assert alert.handle_behaviour_alert_simple.call_count == 1
    assert alert.handle_behaviour_alert_simple.call_args.kwargs["alert_type"] == "motion"


def test_intrusion_alert_promotes_to_alert_engine():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    engine.analyse.return_value = [_alert("intrusion")]
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), _frame())
    # 1 motion log + 1 intrusion alert.
    assert alert.handle_behaviour_alert_simple.call_count == 2
    kinds = [c.kwargs["alert_type"]
             for c in alert.handle_behaviour_alert_simple.call_args_list]
    assert "intrusion" in kinds
    # Synthesised alert carries the zone metadata.
    intrusion_call = next(c for c in alert.handle_behaviour_alert_simple.call_args_list
                          if c.kwargs["alert_type"] == "intrusion")
    details = intrusion_call.kwargs["details"]
    assert details["zone_id"] == "z1"
    assert details["track_id"] == 7
    assert details["class_name"] == "person"


def test_multiple_alerts_in_one_pass_all_emit():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    engine.analyse.return_value = [_alert("intrusion"),
                                   _alert("loitering"),
                                   _alert("line_crossing")]
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), _frame())
    kinds = [c.kwargs["alert_type"]
             for c in alert.handle_behaviour_alert_simple.call_args_list]
    # motion + 3 synthesised.
    assert kinds.count("intrusion") == 1
    assert kinds.count("loitering") == 1
    assert kinds.count("line_crossing") == 1


# ─── cooldown ───────────────────────────────────────────────────────


def test_cooldown_blocks_repeat_runs_within_window():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    engine.analyse.return_value = [_alert()]
    d = _make_dispatcher(engine, router, alert, behavior_cooldown_s=5.0)
    d._behavior_fallback_last_run = 0.0
    d._handle_motion(_motion_event(), _frame())
    d._handle_motion(_motion_event(), _frame())
    d._handle_motion(_motion_event(), _frame())
    assert engine.analyse.call_count == 1


def test_cooldown_passes_after_window():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    engine.analyse.return_value = [_alert()]
    d = _make_dispatcher(engine, router, alert, behavior_cooldown_s=0.01)
    d._handle_motion(_motion_event(), _frame())
    time.sleep(0.05)
    d._handle_motion(_motion_event(), _frame())
    assert engine.analyse.call_count == 2


# ─── failure modes ──────────────────────────────────────────────────


def test_engine_crash_does_not_break_motion_log():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    engine.analyse.side_effect = RuntimeError("CUDA OOM")
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), _frame())
    # Motion log still fires; no synthesised alert.
    assert alert.handle_behaviour_alert_simple.call_count == 1
    assert alert.handle_behaviour_alert_simple.call_args.kwargs["alert_type"] == "motion"


def test_no_router_or_engine_keeps_baseline():
    alert = MagicMock()
    d = _make_dispatcher(behavior_engine=None, capability_router=None,
                          alert_engine=alert)
    d._handle_motion(_motion_event(), _frame())
    # Only the bare motion log.
    assert alert.handle_behaviour_alert_simple.call_count == 1


def test_no_frame_skips_fallback():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), None)
    engine.analyse.assert_not_called()


def test_empty_alert_list_emits_only_motion_log():
    alert = MagicMock()
    router = _router_with(Capability.IVS_RULES, native=False)
    engine = MagicMock()
    engine.analyse.return_value = []
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), _frame())
    assert alert.handle_behaviour_alert_simple.call_count == 1
    assert alert.handle_behaviour_alert_simple.call_args.kwargs["alert_type"] == "motion"
