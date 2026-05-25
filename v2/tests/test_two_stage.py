"""Two-stage AlertEngine: PRELIMINARY → CONFIRMED transitions + finalize.

Driven by Dahua-native handlers — no sensor hub, no per-track behaviour
alerts. The test fakes are deliberately tiny so we exercise the engine,
not the wiring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from itips.alerts.engine import STAGE_CONFIRMED, STAGE_PRELIMINARY, AlertEngine


@dataclass
class _Tenant:
    site_id: str = "site-test"
    operator_id: str = "op-test"
    device_id: str = "jtx-test"


class _FakeIntake:
    def __init__(self):
        self.emitted: list[dict] = []

    def emit(self, *, priority, endpoint_hint, payload, incident_id=None, **kw):
        self.emitted.append({
            "priority": int(priority), "endpoint": endpoint_hint,
            "payload": payload, "incident_id": incident_id,
        })


class _FakeRecorder:
    def __init__(self):
        self.begins: list = []
        self.finishes = 0

    def feed(self, frame):
        pass

    def begin(self, incident_id, package_dir):
        self.begins.append((incident_id, package_dir))

    def finish(self):
        self.finishes += 1


class _FakePackager:
    def __init__(self, tmp_path):
        self.store_root = tmp_path / "evidence"
        (self.store_root / "incidents").mkdir(parents=True)
        self._n = 0
        self.events: list = []
        self.finalized: list = []

    def start_incident(self, *, site_id, operator_id, device_id):
        self._n += 1
        incident_id = f"incident-{self._n}"
        (self.store_root / "incidents" / incident_id).mkdir(parents=True, exist_ok=True)
        return incident_id

    def attach_event(self, incident_id, event):
        self.events.append((incident_id, event))

    def attach_file(self, *args, **kw):
        pass

    def finalize(self, incident_id, timeout=30.0):
        self.finalized.append(incident_id)
        return self.store_root / "incidents" / incident_id


def _engine(tmp_path, *, dwell=0.5, idle=0.5):
    intake = _FakeIntake()
    recorder = _FakeRecorder()
    packager = _FakePackager(tmp_path)
    engine = AlertEngine(
        intake=intake, evidence_packager=packager, tenant=_Tenant(),
        recorders={1: recorder, 2: recorder},
        confirmation_dwell_seconds=dwell,
        confirmation_window_seconds=30.0,
        idle_timeout_seconds=idle,
    )
    return engine, intake, recorder, packager


def _stage_for(engine, camera_id):
    for state in engine.active_incidents():
        if state["camera_id"] == camera_id:
            return state["stage"]
    return None


def test_camera_event_opens_preliminary_and_starts_recording(tmp_path):
    engine, intake, recorder, _ = _engine(tmp_path)
    engine.handle_behaviour_alert_simple(
        camera_id=1, alert_type="intrusion", details={"rule_name": "perimeter"},
    )
    assert _stage_for(engine, 1) == STAGE_PRELIMINARY
    assert len(recorder.begins) == 1
    endpoints = [e["endpoint"] for e in intake.emitted]
    assert "A3" in endpoints and "A4" in endpoints


def test_face_intruder_promotes(tmp_path):
    engine, _, _, _ = _engine(tmp_path, dwell=999)
    engine.handle_face_intruder(camera_id=2, face_bbox=(10, 10, 20, 20), name="INTRUDER")
    assert _stage_for(engine, 2) == STAGE_CONFIRMED


def test_loitering_promotes_immediately(tmp_path):
    engine, _, _, _ = _engine(tmp_path, dwell=999)
    engine.handle_behaviour_alert_simple(
        camera_id=1, alert_type="loitering", details={},
    )
    # loitering is camera-classified — no Jetson dwell needed.
    assert _stage_for(engine, 1) == STAGE_CONFIRMED


def test_fire_promotes_immediately(tmp_path):
    engine, intake, _, _ = _engine(tmp_path, dwell=999)
    engine.handle_fire(camera_id=1, details={"rule": "FireDetection"})
    assert _stage_for(engine, 1) == STAGE_CONFIRMED
    confirmed = [e for e in intake.emitted if e["endpoint"] == "A3"
                 and e["payload"].get("incident_type") == "confirmed_incident"]
    assert confirmed
    assert confirmed[0]["payload"]["confirmation_signal"] == "fire"


def test_personnel_seen_does_not_open_incident(tmp_path):
    engine, _, _, _ = _engine(tmp_path)
    engine.handle_personnel_seen(
        camera_id=1, person_uid="0005", group_id="10000",
        name="Worker A", similarity=88,
    )
    assert engine.active_incidents() == []


def test_dwell_promotes_after_repeated_events(tmp_path):
    engine, _, _, _ = _engine(tmp_path, dwell=0.2)
    engine.handle_behaviour_alert_simple(
        camera_id=1, alert_type="intrusion", details={},
    )
    assert _stage_for(engine, 1) == STAGE_PRELIMINARY
    time.sleep(0.3)
    engine.handle_behaviour_alert_simple(
        camera_id=1, alert_type="intrusion", details={},
    )
    assert _stage_for(engine, 1) == STAGE_CONFIRMED


def test_idle_timeout_finalizes_incident(tmp_path):
    engine, intake, recorder, packager = _engine(tmp_path, dwell=999, idle=0.3)
    engine.start()
    try:
        engine.handle_behaviour_alert_simple(
            camera_id=1, alert_type="intrusion", details={},
        )
        time.sleep(1.5)
    finally:
        engine.stop()
    assert packager.finalized == ["incident-1"]
    assert recorder.finishes == 1
    pkg = [e for e in intake.emitted if e["endpoint"] == "A5"]
    assert len(pkg) == 1
