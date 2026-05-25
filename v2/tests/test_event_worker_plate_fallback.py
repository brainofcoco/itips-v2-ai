"""Event-worker plate fallback wiring.

Drives `_handle_vehicle_gate` (CarDrivingInOut, strong signal) and
`_handle_motion` (VideoMotion, weak signal + cooldown) with a stub
PlateEngine + CapabilityRouter to confirm the dispatcher routes a
read plate through the same `handle_plate_capture` path that the
native TrafficCarMeasurement event uses.

No EasyOCR, no Dahua. Pure routing test.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np

from itips.ml.capability_router import Capability, CapabilityRouter, CapabilitySnapshot
from itips.ml.plate_engine import PlateReadResult
from itips.runtime.event_worker import DahuaEventDispatcher, WorkerDeps
from itips.sensors.dahua_events import DahuaEvent


def _vehicle_event(direction: int = 1) -> DahuaEvent:
    return DahuaEvent(
        camera_id=1, code="CarDrivingInOut", action="Pulse", index=0,
        data={"DrivingDirection": direction},
    )


def _motion_event() -> DahuaEvent:
    return DahuaEvent(
        camera_id=1, code="VideoMotion", action="Start", index=0, data={},
    )


def _make_dispatcher(plate_engine, capability_router, alert_engine):
    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=MagicMock(),
        recorders={},
        event_tap=None,
        capability_router=capability_router,
        plate_engine=plate_engine,
    )
    d = DahuaEventDispatcher.__new__(DahuaEventDispatcher)
    d.camera_id = 1
    d.deps = deps
    d._plate_fallback_cooldown_s = 10.0
    d._plate_fallback_last_run = 0.0
    return d


def _frame():
    return np.zeros((480, 640, 3), dtype="uint8")


def _router_with(cap: Capability, native: bool) -> CapabilityRouter:
    r = CapabilityRouter()
    r.set_camera(CapabilitySnapshot(camera_id=1, native={cap: native}))
    return r


def _plate_read(plate="LAG123XY", conf=0.85) -> PlateReadResult:
    return PlateReadResult(
        plate_number=plate, confidence=conf,
        bbox=(10.0, 10.0, 110.0, 40.0), raw_text=plate,
    )


# ─── CarDrivingInOut (strong signal — always runs, no cooldown) ──────


def test_native_anpr_path_does_not_call_plate_engine():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=True)
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)

    d._handle_vehicle_gate(_vehicle_event(direction=1), _frame())

    engine.read_plate.assert_not_called()
    # vehicle_gate still fires its bare-event log on every path.
    alert.handle_behaviour_alert_simple.assert_called_once()


def test_anpr_fallback_on_vehicle_gate_promotes_to_plate_capture():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    engine.read_plate.return_value = _plate_read()
    d = _make_dispatcher(engine, router, alert)

    d._handle_vehicle_gate(_vehicle_event(direction=1), _frame())

    engine.read_plate.assert_called_once()
    alert.handle_plate_capture.assert_called_once()
    kw = alert.handle_plate_capture.call_args.kwargs
    assert kw["plate_number"] == "LAG123XY"
    assert kw["camera_id"] == 1


def test_vehicle_gate_ignores_plate_cooldown():
    """CarDrivingInOut is a strong, infrequent signal — no debounce."""
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    engine.read_plate.return_value = _plate_read()
    d = _make_dispatcher(engine, router, alert)
    d._plate_fallback_last_run = time.monotonic()  # pretend we just ran

    d._handle_vehicle_gate(_vehicle_event(direction=1), _frame())
    assert engine.read_plate.call_count == 1


def test_no_plate_read_means_no_capture_alert():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    engine.read_plate.return_value = None  # OCR found no plate
    d = _make_dispatcher(engine, router, alert)

    d._handle_vehicle_gate(_vehicle_event(), _frame())

    alert.handle_plate_capture.assert_not_called()
    # vehicle_gate's bare log still fires.
    alert.handle_behaviour_alert_simple.assert_called_once()


# ─── VideoMotion (weak signal — cooldown enforced) ───────────────────


def test_motion_runs_plate_fallback_once_then_debounces():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    engine.read_plate.return_value = _plate_read()
    d = _make_dispatcher(engine, router, alert)
    d._plate_fallback_cooldown_s = 5.0
    d._plate_fallback_last_run = 0.0

    d._handle_motion(_motion_event(), _frame())
    d._handle_motion(_motion_event(), _frame())  # within cooldown
    d._handle_motion(_motion_event(), _frame())  # within cooldown

    assert engine.read_plate.call_count == 1
    assert alert.handle_plate_capture.call_count == 1


def test_motion_runs_plate_fallback_again_after_cooldown():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    engine.read_plate.return_value = _plate_read()
    d = _make_dispatcher(engine, router, alert)
    d._plate_fallback_cooldown_s = 0.01  # tiny — finishes in test time
    d._plate_fallback_last_run = 0.0

    d._handle_motion(_motion_event(), _frame())
    time.sleep(0.05)
    d._handle_motion(_motion_event(), _frame())

    assert engine.read_plate.call_count == 2


def test_motion_does_not_call_engine_when_native_anpr_present():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=True)
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)
    d._handle_motion(_motion_event(), _frame())
    engine.read_plate.assert_not_called()


# ─── failure modes ──────────────────────────────────────────────────


def test_engine_crash_does_not_break_lifecycle():
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    engine.read_plate.side_effect = RuntimeError("CUDA OOM")
    d = _make_dispatcher(engine, router, alert)

    d._handle_vehicle_gate(_vehicle_event(), _frame())

    alert.handle_plate_capture.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_called_once()  # still logs


def test_no_router_or_engine_keeps_baseline():
    """Vanilla v2 with no ML wired — handlers behave as before."""
    alert = MagicMock()
    d = _make_dispatcher(plate_engine=None, capability_router=None,
                          alert_engine=alert)
    d._handle_vehicle_gate(_vehicle_event(), _frame())
    d._handle_motion(_motion_event(), _frame())
    assert alert.handle_plate_capture.call_count == 0
    assert alert.handle_behaviour_alert_simple.call_count == 2


def test_no_frame_means_no_fallback():
    """Without a frame we can't OCR — skip cleanly."""
    alert = MagicMock()
    router = _router_with(Capability.ANPR, native=False)
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)

    d._handle_vehicle_gate(_vehicle_event(), None)
    engine.read_plate.assert_not_called()
