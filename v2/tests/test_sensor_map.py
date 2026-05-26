"""SensorMap — JSON persistence + validation."""

from __future__ import annotations

import pytest

from itips.sensors.sensor_map import SensorMap, SensorMapping


def test_construction_requires_preset_name():
    with pytest.raises(ValueError):
        SensorMapping(zone_id=1, camera_id=4, preset_name="")


def test_construction_requires_positive_camera_id():
    with pytest.raises(ValueError):
        SensorMapping(zone_id=1, camera_id=0, preset_name="Gate")


def test_upsert_get_roundtrip(tmp_path):
    sm = SensorMap(path=tmp_path / "sensors.json")
    sm.upsert(SensorMapping(zone_id=1, camera_id=4, preset_name="Gate View",
                            sensor_type="doorContact", description="Front gate"))
    got = sm.get(1)
    assert got is not None
    assert got.camera_id == 4
    assert got.preset_name == "Gate View"
    assert got.sensor_type == "doorContact"


def test_upsert_replaces_in_place(tmp_path):
    sm = SensorMap(path=tmp_path / "sensors.json")
    sm.upsert(SensorMapping(zone_id=1, camera_id=1, preset_name="A"))
    sm.upsert(SensorMapping(zone_id=1, camera_id=4, preset_name="B"))
    assert len(sm) == 1
    assert sm.get(1).camera_id == 4
    assert sm.get(1).preset_name == "B"


def test_remove(tmp_path):
    sm = SensorMap(path=tmp_path / "sensors.json")
    sm.upsert(SensorMapping(zone_id=1, camera_id=4, preset_name="Gate"))
    assert sm.remove(1) is True
    assert sm.remove(1) is False
    assert sm.get(1) is None


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "sensors.json"
    sm1 = SensorMap(path=path)
    sm1.upsert(SensorMapping(zone_id=3, camera_id=4, preset_name="Generator",
                              sensor_type="pircam"))
    sm2 = SensorMap(path=path)
    got = sm2.get(3)
    assert got is not None
    assert got.preset_name == "Generator"
    assert got.sensor_type == "pircam"


def test_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text("{not valid json", encoding="utf-8")
    sm = SensorMap(path=path)
    assert sm.all() == []


def test_invalid_entries_are_skipped_at_load(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text('{"1": {"camera_id": 4, "preset_name": "OK"},'
                    ' "2": {"camera_id": 0, "preset_name": "bad-camera"},'
                    ' "x": {"camera_id": 1, "preset_name": "bad-key"}}',
                    encoding="utf-8")
    sm = SensorMap(path=path)
    assert len(sm) == 1
    assert sm.get(1) is not None


def test_all_returns_sorted_by_zone_id(tmp_path):
    sm = SensorMap(path=tmp_path / "sensors.json")
    sm.upsert(SensorMapping(zone_id=5, camera_id=1, preset_name="Z"))
    sm.upsert(SensorMapping(zone_id=2, camera_id=1, preset_name="A"))
    sm.upsert(SensorMapping(zone_id=9, camera_id=1, preset_name="M"))
    ids = [m.zone_id for m in sm.all()]
    assert ids == [2, 5, 9]
