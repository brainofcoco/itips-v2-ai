"""SensorDispatcher — the pan → snapshot → evaluator pipeline.

We exercise `_process()` synchronously (no thread start) with mocked
PTZ, snapshot, ThreatEvaluator, and AlertEngine. That isolates the
routing logic from real cameras and from the worker thread's queue.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from itips.runtime.sensor_dispatcher import SensorDispatcher
from itips.sensors.sensor_event import SensorEvent, SensorEventTap
from itips.sensors.sensor_map import SensorMap, SensorMapping


def _frame():
    return np.zeros((480, 640, 3), dtype="uint8")


def _make_dispatcher(tmp_path,
                     *,
                     mapping=None,
                     evaluator=None,
                     pan_ok=True,
                     snapshot=_frame()):
    sensor_map = SensorMap(path=tmp_path / "sensors.json")
    if mapping:
        sensor_map.upsert(mapping)
    event_tap = SensorEventTap(capacity=20)
    alert = MagicMock()
    # Fake DahuaManager.get returns a client with .ptz + .endpoint.
    client = MagicMock()
    client.ptz.goto_preset_by_name.return_value = pan_ok
    client.endpoint.snapshot.return_value = snapshot
    manager = MagicMock()
    manager.get.return_value = client
    d = SensorDispatcher(
        alert_engine=alert,
        dahua_manager=manager,
        sensor_map=sensor_map,
        event_tap=event_tap,
        threat_evaluator=evaluator,
        pan_settle_s=0.0,           # don't block tests
        snapshot_timeout_s=0.5,
        per_zone_cooldown_s=0.0,    # opt-out for clarity
    )
    return d, alert, manager, client, event_tap


def _evt(zone_id=1, event_type="doorContact") -> SensorEvent:
    return SensorEvent(zone_id=zone_id, event_type=event_type,
                       zone_name=f"zone-{zone_id}")


# ─── always-log sensor alarm ────────────────────────────────────────


def test_sensor_alarm_logs_even_when_unmapped(tmp_path):
    d, alert, *_ = _make_dispatcher(tmp_path)  # no mapping
    d._process(_evt(zone_id=99))
    # Bare sensor_alarm fired regardless of mapping.
    kinds = [c.kwargs["alert_type"]
             for c in alert.handle_behaviour_alert_simple.call_args_list]
    assert "sensor_alarm" in kinds


def test_unmapped_zone_skips_pan_and_evaluator(tmp_path):
    evaluator = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, evaluator=evaluator,
    )
    d._process(_evt(zone_id=99))
    client.ptz.goto_preset_by_name.assert_not_called()
    client.endpoint.snapshot.assert_not_called()
    evaluator.trigger.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "unmapped"


# ─── happy path — hand frame to evaluator ────────────────────────────


def test_successful_pipeline_triggers_evaluator(tmp_path):
    mapping = SensorMapping(zone_id=3, camera_id=4, preset_name="Gate")
    evaluator = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, evaluator=evaluator,
    )
    d._process(_evt(zone_id=3))
    client.ptz.goto_preset_by_name.assert_called_once_with("Gate")
    client.endpoint.snapshot.assert_called_once()
    evaluator.trigger.assert_called_once()
    kw = evaluator.trigger.call_args.kwargs
    assert kw["camera_id"] == 4
    assert kw["trigger_kind"] == "sensor:zone-3"
    assert kw["initial_frame"] is not None
    assert kw["details"]["zone_id"] == 3
    assert kw["details"]["preset_name"] == "Gate"
    assert tap.recent()[0]["outcome"]["verdict"] == "evaluating"


# ─── failure modes ──────────────────────────────────────────────────


def test_pan_failure_skips_snapshot_and_evaluator(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    evaluator = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, evaluator=evaluator, pan_ok=False,
    )
    d._process(_evt(zone_id=1))
    client.endpoint.snapshot.assert_not_called()
    evaluator.trigger.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "pan_failed"


def test_snapshot_failure_skips_evaluator(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    evaluator = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, evaluator=evaluator, snapshot=None,
    )
    d._process(_evt(zone_id=1))
    evaluator.trigger.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "snapshot_failed"


def test_unknown_camera_skips_pan(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=99, preset_name="Gate")
    evaluator = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, evaluator=evaluator,
    )
    manager.get.return_value = None   # camera not registered
    d._process(_evt(zone_id=1))
    client.ptz.goto_preset_by_name.assert_not_called()
    evaluator.trigger.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "no_camera"


# ─── evaluator missing (degraded mode) ──────────────────────────────


def test_no_evaluator_logs_unverified_after_capturing_evidence(tmp_path):
    """Evaluator absent (face engine not loaded). Pan + snapshot still
    run so operators have a frame to review, and sensor_unverified fires
    in the audit log so they know why no verdict arrived."""
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, evaluator=None,
    )
    d._process(_evt(zone_id=1))
    client.ptz.goto_preset_by_name.assert_called_once()
    client.endpoint.snapshot.assert_called_once()
    kinds = [c.kwargs["alert_type"]
             for c in alert.handle_behaviour_alert_simple.call_args_list]
    assert "sensor_unverified" in kinds
    assert tap.recent()[0]["outcome"]["verdict"] == "unverified_no_engine"


# ─── cooldown ───────────────────────────────────────────────────────


def test_per_zone_cooldown_drops_rapid_retriggers(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    evaluator = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, evaluator=evaluator,
    )
    d._per_zone_cooldown_s = 30.0   # long enough to bite
    d._process(_evt(zone_id=1))     # first one — runs end-to-end
    d._process(_evt(zone_id=1))     # second — should be blocked
    # Pan called exactly once.
    assert client.ptz.goto_preset_by_name.call_count == 1
    assert evaluator.trigger.call_count == 1
    # Newest entry on the tap is the cooldown one.
    assert tap.recent()[0]["outcome"]["verdict"] == "cooldown"
