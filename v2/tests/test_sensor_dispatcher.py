"""SensorDispatcher — the pan → snapshot → face-validate pipeline.

We exercise `_process()` synchronously (no thread start) with mocked
PTZ, snapshot, FaceEngine, and AlertEngine. That isolates the routing
logic from real cameras and from the worker thread's queue.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from itips.ml.face_engine import RecognitionResult
from itips.runtime.sensor_dispatcher import SensorDispatcher
from itips.sensors.sensor_event import SensorEvent, SensorEventTap
from itips.sensors.sensor_map import SensorMap, SensorMapping


def _frame():
    return np.zeros((480, 640, 3), dtype="uint8")


def _make_dispatcher(tmp_path,
                     *,
                     mapping=None,
                     face_engine=None,
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
        face_engine=face_engine,
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


def test_unmapped_zone_skips_pan_and_recognise(tmp_path):
    d, alert, manager, client, tap = _make_dispatcher(tmp_path)
    d._process(_evt(zone_id=99))
    client.ptz.goto_preset_by_name.assert_not_called()
    client.endpoint.snapshot.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "unmapped"


# ─── happy paths ────────────────────────────────────────────────────


def test_matched_person_fires_personnel_seen(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    face = MagicMock()
    face.recognize.return_value = RecognitionResult(
        matched=True, person_id="p-42", full_name="Sam",
        similarity=0.81, embedding=np.zeros(512, dtype="float32"),
    )
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=face,
    )
    d._process(_evt(zone_id=1))
    client.ptz.goto_preset_by_name.assert_called_once_with("Gate")
    client.endpoint.snapshot.assert_called_once()
    face.recognize.assert_called_once()
    alert.handle_personnel_seen.assert_called_once()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "p-42"
    assert kw["name"] == "Sam"
    assert kw["group_id"] == "jetson-sensor-validated"
    assert kw["camera_id"] == 4
    assert tap.recent()[0]["outcome"]["verdict"] == "authorised"


def test_no_match_fires_face_intruder(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    face = MagicMock()
    face.recognize.return_value = RecognitionResult(
        matched=False, person_id=None, full_name=None,
        similarity=0.12,
        embedding=np.zeros(512, dtype="float32"),   # face present but no match
    )
    d, alert, *_, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=face,
    )
    d._process(_evt(zone_id=1))
    alert.handle_face_intruder.assert_called_once()
    alert.handle_personnel_seen.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "intruder"


def test_no_face_in_frame_fires_unverified(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    face = MagicMock()
    face.recognize.return_value = RecognitionResult(
        matched=False, person_id=None, full_name=None,
        similarity=0.0, embedding=None,    # no embedding ⇒ no face found
    )
    d, alert, *_, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=face,
    )
    d._process(_evt(zone_id=1))
    # sensor_alarm + sensor_unverified, no personnel_seen, no intruder.
    kinds = [c.kwargs["alert_type"]
             for c in alert.handle_behaviour_alert_simple.call_args_list]
    assert kinds == ["sensor_alarm", "sensor_unverified"]
    alert.handle_personnel_seen.assert_not_called()
    alert.handle_face_intruder.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "unverified_no_face"


# ─── failure modes ──────────────────────────────────────────────────


def test_pan_failure_skips_snapshot_and_validation(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    face = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=face, pan_ok=False,
    )
    d._process(_evt(zone_id=1))
    client.endpoint.snapshot.assert_not_called()
    face.recognize.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "pan_failed"


def test_snapshot_failure_skips_validation(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    face = MagicMock()
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=face, snapshot=None,
    )
    d._process(_evt(zone_id=1))
    face.recognize.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "snapshot_failed"


def test_engine_unwired_still_logs_unverified_and_keeps_pan_evidence(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=None,
    )
    d._process(_evt(zone_id=1))
    # Pan + snapshot still ran (operator has evidence to review).
    client.ptz.goto_preset_by_name.assert_called_once()
    client.endpoint.snapshot.assert_called_once()
    # But no validation outcome — sensor_unverified fires.
    kinds = [c.kwargs["alert_type"]
             for c in alert.handle_behaviour_alert_simple.call_args_list]
    assert "sensor_unverified" in kinds
    assert tap.recent()[0]["outcome"]["verdict"] == "unverified_no_engine"


def test_unknown_camera_skips_pan(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=99, preset_name="Gate")
    d, alert, manager, client, tap = _make_dispatcher(tmp_path, mapping=mapping)
    manager.get.return_value = None   # camera not registered
    d._process(_evt(zone_id=1))
    client.ptz.goto_preset_by_name.assert_not_called()
    assert tap.recent()[0]["outcome"]["verdict"] == "no_camera"


# ─── cooldown ───────────────────────────────────────────────────────


def test_per_zone_cooldown_drops_rapid_retriggers(tmp_path):
    mapping = SensorMapping(zone_id=1, camera_id=4, preset_name="Gate")
    face = MagicMock()
    face.recognize.return_value = RecognitionResult(
        matched=True, person_id="p", full_name="X",
        similarity=0.9, embedding=np.zeros(512, dtype="float32"),
    )
    d, alert, manager, client, tap = _make_dispatcher(
        tmp_path, mapping=mapping, face_engine=face,
    )
    d._per_zone_cooldown_s = 30.0   # long enough to bite
    d._process(_evt(zone_id=1))     # first one — runs end-to-end
    d._process(_evt(zone_id=1))     # second — should be blocked
    # Pan called exactly once.
    assert client.ptz.goto_preset_by_name.call_count == 1
    # Sensor_alarm fired both times (audit trail), but the second
    # event_tap entry is the cooldown one (newest first).
    assert tap.recent()[0]["outcome"]["verdict"] == "cooldown"
