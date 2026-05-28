"""B1/B3/B4 inbound API handlers — pure-function tests.

The route handlers in `itips.api.inbound` accept the Dahua manager and
the personnel store as dependencies, so we can swap in fakes and not
touch a real camera.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from itips.api.inbound import (
    _apply_command,
    _apply_maintenance,
    _apply_personnel_sync,
)
from itips.api.personnel_store import PersonnelStore


@dataclass
class _FakeFaceEngine:
    """Mimics itips.ml.face_engine.FaceEngine's enrol/remove surface."""
    enrolled: dict = field(default_factory=dict)  # person_id -> full_name
    no_face: bool = False

    def enroll(self, *, person_id, full_name, image_bytes):
        if self.no_face:
            raise ValueError("no face found in enrolment image")
        self.enrolled[person_id] = full_name

    def remove(self, person_id):
        return self.enrolled.pop(person_id, None) is not None


@dataclass
class _FakePTZ:
    overrides: list = field(default_factory=list)

    is_connected = True

    def apply_override(self, params):
        self.overrides.append(params)


@dataclass
class _FakeDeterrence:
    fired: list = field(default_factory=list)
    standdowns: int = 0

    def fire(self, *, light=True, speaker=True):
        self.fired.append((light, speaker))

    def standdown(self):
        self.standdowns += 1


@dataclass
class _FakeClient:
    camera_id: int
    ptz: _FakePTZ = field(default_factory=_FakePTZ)
    deterrence: _FakeDeterrence = field(default_factory=_FakeDeterrence)


class _FakeDahua:
    def __init__(self, cameras):
        self._cameras = cameras

    def all(self):
        return list(self._cameras.values())

    def get(self, camera_id):
        return self._cameras.get(camera_id)

    def camera_ids(self):
        return sorted(self._cameras.keys())


def _jpeg_b64() -> str:
    return base64.b64encode(b"\xff\xd8\xff\xe0not really a jpeg but it's bytes").decode("ascii")


def test_personnel_sync_enrolls_into_face_engine(tmp_path):
    engine = _FakeFaceEngine()
    store = PersonnelStore(db_path=tmp_path / "p.sqlite")
    result = _apply_personnel_sync(
        {"action": "add", "person_id": "p1", "full_name": "Worker A",
         "image_b64": _jpeg_b64()},
        store, engine,
    )
    assert result["synced"] is True
    assert engine.enrolled == {"p1": "Worker A"}
    record = store.get("p1")
    assert record is not None
    assert record.full_name == "Worker A"


def test_personnel_sync_deactivate_removes_embedding(tmp_path):
    engine = _FakeFaceEngine()
    store = PersonnelStore(db_path=tmp_path / "p.sqlite")
    _apply_personnel_sync(
        {"action": "add", "person_id": "p1", "full_name": "X",
         "image_b64": _jpeg_b64()}, store, engine,
    )
    result = _apply_personnel_sync(
        {"action": "deactivate", "person_id": "p1"}, store, engine,
    )
    assert result["synced"] is True
    assert store.get("p1") is None
    assert "p1" not in engine.enrolled


def test_personnel_sync_no_face_engine_does_not_sync(tmp_path):
    store = PersonnelStore(db_path=tmp_path / "p.sqlite")
    result = _apply_personnel_sync(
        {"action": "add", "person_id": "p1", "full_name": "Worker A",
         "image_b64": _jpeg_b64()},
        store, None,
    )
    assert result["synced"] is False
    assert store.get("p1") is None


def test_personnel_sync_no_detectable_face_does_not_sync(tmp_path):
    engine = _FakeFaceEngine(no_face=True)
    store = PersonnelStore(db_path=tmp_path / "p.sqlite")
    result = _apply_personnel_sync(
        {"action": "add", "person_id": "p1", "full_name": "Worker A",
         "image_b64": _jpeg_b64()},
        store, engine,
    )
    assert result["synced"] is False
    assert store.get("p1") is None


def test_personnel_sync_rejects_missing_fields(tmp_path):
    store = PersonnelStore(db_path=tmp_path / "p.sqlite")
    result = _apply_personnel_sync({"action": "add"}, store, _FakeFaceEngine())
    assert result["synced"] is False


def test_maintenance_window_is_acknowledged():
    assert _apply_maintenance({
        "action": "arm", "window_id": "w1", "person_id": "p1",
    }) == {"applied": True}


def test_command_ptz_override():
    cam1 = _FakeClient(1)
    dahua = _FakeDahua({1: cam1})
    result = _apply_command(
        {"command_type": "ptz_override", "parameters": {"camera_id": 1, "preset_id": 3}},
        dahua,
    )
    assert result == {"command_accepted": True}
    assert cam1.ptz.overrides == [{"camera_id": 1, "preset_id": 3}]


def test_command_deterrence_fire():
    cam1 = _FakeClient(1)
    dahua = _FakeDahua({1: cam1})
    result = _apply_command(
        {"command_type": "deterrence_fire",
         "parameters": {"camera_id": 1, "light": True, "speaker": False}},
        dahua,
    )
    assert result == {"command_accepted": True}
    assert cam1.deterrence.fired == [(True, False)]


def test_command_deterrence_standdown():
    cam1 = _FakeClient(1)
    dahua = _FakeDahua({1: cam1})
    _apply_command(
        {"command_type": "deterrence_standdown", "parameters": {"camera_id": 1}},
        dahua,
    )
    assert cam1.deterrence.standdowns == 1


def test_command_request_stream_returns_url_hint():
    result = _apply_command(
        {"command_type": "request_stream", "parameters": {"camera_id": 2}},
        _FakeDahua({}),
    )
    assert result["command_accepted"] is True
    assert result["stream"]["url_hint"] == "/video_feed/2"


def test_command_unknown_rejected():
    result = _apply_command({"command_type": "what"}, _FakeDahua({}))
    assert result["command_accepted"] is False
