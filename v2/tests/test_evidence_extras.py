"""Evidence package extras — H.265 fallback, face/plate JPEGs,
sensor_log split, chain-of-custody metadata, status marker,
incident_summary.pdf.

Real ffmpeg is not required for these tests — when missing, the
H.265 writer transparently falls back to cv2/mp4v and tests still pass
because they only check that *something* writeable was returned.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from itips.evidence.packager import (
    EvidencePackager, _is_sensor_event, _safe_filename_ts,
)
from itips.evidence.summary import build_pdf_summary, build_highlight
from itips.evidence.video_writer import open_writer


# ─── helpers ────────────────────────────────────────────────────────


@pytest.fixture
def packager(tmp_path, monkeypatch):
    monkeypatch.setenv("ITIPS_DEVICE_HMAC_KEY", "deadbeef" * 8)
    p = EvidencePackager(store_root=tmp_path / "ev", pre_event_seconds=10,
                          post_event_seconds=10)
    p.start()
    yield p
    p.stop()
    p.join(timeout=2.0)


def _open_incident(packager) -> str:
    incident_id = packager.start_incident(
        site_id="site-1", operator_id="op-1", device_id="dev-1",
    )
    # Wait for the start op to land so attach_* are not no-ops.
    for _ in range(40):
        if (Path(packager.store_root) / "incidents" / incident_id).exists():
            break
        time.sleep(0.025)
    return incident_id


def _wait_finalize(packager, incident_id, **kw) -> Path:
    return packager.finalize(incident_id, timeout=10.0, **kw)


# ─── helper functions ──────────────────────────────────────────────


def test_safe_filename_ts_strips_colons():
    assert ":" not in _safe_filename_ts("2026-05-26T01:02:03Z")


def test_is_sensor_event_matches_kind_and_alert_type():
    assert _is_sensor_event({"kind": "sensor_alarm"}) is True
    assert _is_sensor_event({"alert_type": "sensor_unverified"}) is True
    assert _is_sensor_event({"kind": "face_intruder"}) is False


# ─── video writer fallback ─────────────────────────────────────────


def test_video_writer_opens_something(tmp_path):
    """Either ffmpeg's libx265 or cv2/mp4v — we just want a writable handle."""
    import numpy as np
    path = tmp_path / "clip.mp4"
    w = open_writer(path, fps=10.0, size=(320, 240))
    assert w.isOpened()
    for _ in range(15):
        w.write(np.zeros((240, 320, 3), dtype="uint8"))
    w.release()
    assert path.exists() and path.stat().st_size > 0


# ─── face / plate capture persistence ──────────────────────────────


def test_attach_face_capture_writes_jpeg_and_indexes_manifest(packager):
    incident_id = _open_incident(packager)
    packager.attach_face_capture(
        incident_id, jpeg=b"\xff\xd8\xff\xe0fake-jpeg",
        confidence=0.83, name="Sam",
    )
    pkg = _wait_finalize(packager, incident_id, closed_reason="test")
    faces = list((pkg / "face_captures").iterdir())
    assert len(faces) == 1
    manifest = json.loads((pkg / "manifest.json").read_text())
    kinds = [f["kind"] for f in manifest["files"]]
    assert "face_capture" in kinds


def test_attach_plate_capture_writes_jpeg_and_indexes_manifest(packager):
    incident_id = _open_incident(packager)
    packager.attach_plate_capture(
        incident_id, jpeg=b"\xff\xd8\xff\xe0fake-jpeg",
        plate_number="LAG-123-XY", confidence=0.91,
    )
    pkg = _wait_finalize(packager, incident_id)
    plates = list((pkg / "plate_captures").iterdir())
    assert len(plates) == 1
    assert "LAG" in plates[0].name and "XY" in plates[0].name
    manifest = json.loads((pkg / "manifest.json").read_text())
    kinds = [f["kind"] for f in manifest["files"]]
    assert "plate_capture" in kinds


def test_multiple_face_captures_increment_sequence(packager):
    incident_id = _open_incident(packager)
    for i in range(3):
        packager.attach_face_capture(
            incident_id, jpeg=b"\xff\xd8\xff\xe0a",
            confidence=0.5, name=f"p{i}",
        )
    pkg = _wait_finalize(packager, incident_id)
    faces = sorted(p.name for p in (pkg / "face_captures").iterdir())
    assert faces[0].startswith("face_001_")
    assert faces[1].startswith("face_002_")
    assert faces[2].startswith("face_003_")


# ─── metadata enrichment ───────────────────────────────────────────


def test_finalize_populates_status_and_chain_of_custody(packager):
    incident_id = _open_incident(packager)
    packager.attach_event(incident_id, {"camera_id": 4, "kind": "face_detected"})
    packager.attach_event(incident_id, {"camera_id": 1, "kind": "motion"})
    packager.attach_event(incident_id, {
        "kind": "stage_change", "stage": "CONFIRMED",
        "signal": "face_intruder", "timestamp_utc": "2026-05-26T01:00:00Z",
    })
    packager.update_metadata(incident_id, {
        "incident_classification": "perimeter_breach",
        "gps_coordinates": [6.5244, 3.3792],
    })
    packager.attach_face_capture(incident_id, jpeg=b"x", confidence=0.7, name="x")
    pkg = _wait_finalize(packager, incident_id, closed_reason="janitor")

    meta = json.loads((pkg / "incident_metadata.json").read_text())
    assert meta["status"] == "complete"
    assert meta["closed_reason"] == "janitor"
    assert "finalized_utc" in meta
    assert meta["camera_ids_active"] == [1, 4]
    assert meta["incident_classification"] == "perimeter_breach"
    assert meta["gps_coordinates"] == [6.5244, 3.3792]
    assert meta["face_capture_count"] == 1
    assert meta["event_count"] >= 3
    # alert_stage_log populated from stage_change events.
    stages = [s["signal"] for s in meta["alert_stage_log"]]
    assert "face_intruder" in stages


# ─── sensor_log.json split ─────────────────────────────────────────


def test_finalize_writes_sensor_log_when_sensor_events_present(packager):
    incident_id = _open_incident(packager)
    packager.attach_event(incident_id, {"kind": "sensor_alarm", "zone_id": 1,
                                          "camera_id": 4})
    packager.attach_event(incident_id, {"kind": "face_detected", "camera_id": 4})
    packager.attach_event(incident_id, {"kind": "sensor_unverified",
                                          "camera_id": 4, "zone_id": 1})
    pkg = _wait_finalize(packager, incident_id)

    sensor_log_path = pkg / "sensor_log.json"
    assert sensor_log_path.exists()
    sensors = json.loads(sensor_log_path.read_text())
    kinds = [e["kind"] for e in sensors]
    assert sorted(kinds) == ["sensor_alarm", "sensor_unverified"]

    # Sensor entries also remain in the unified event_log.
    event_log = json.loads((pkg / "event_log.json").read_text())
    assert len(event_log) >= 3

    # Manifest indexes sensor_log.json.
    manifest = json.loads((pkg / "manifest.json").read_text())
    kinds = [f["kind"] for f in manifest["files"]]
    assert "sensor_log" in kinds


def test_no_sensor_log_when_no_sensor_events(packager):
    incident_id = _open_incident(packager)
    packager.attach_event(incident_id, {"kind": "face_detected", "camera_id": 4})
    pkg = _wait_finalize(packager, incident_id)
    assert not (pkg / "sensor_log.json").exists()


# ─── signature still valid after all the new artefacts ─────────────


def test_signature_includes_new_artefacts_in_manifest(packager):
    incident_id = _open_incident(packager)
    packager.attach_face_capture(incident_id, jpeg=b"x", confidence=0.6, name="x")
    packager.attach_plate_capture(incident_id, jpeg=b"y",
                                    plate_number="LAG12XY", confidence=0.7)
    packager.attach_event(incident_id, {"kind": "sensor_alarm", "camera_id": 1})
    pkg = _wait_finalize(packager, incident_id)
    signature = json.loads((pkg / "signature.sha256").read_text())
    manifest = json.loads((pkg / "manifest.json").read_text())
    files = {f["filename"] for f in manifest["files"]}
    # All canonical PRD artefacts referenced in the signed manifest.
    assert "incident_metadata.json" in files
    assert "event_log.json" in files
    assert "sensor_log.json" in files
    assert any("face_captures/" in f for f in files)
    assert any("plate_captures/" in f for f in files)
    assert signature["algorithm"] == "HMAC-SHA-256"
    assert signature["signature"]
    assert signature["manifest_hash"]


# ─── summary.build_pdf_summary ─────────────────────────────────────


def test_pdf_summary_writes_a_valid_pdf(tmp_path):
    pdf_path = build_pdf_summary(
        package_dir=tmp_path,
        metadata={
            "incident_id": "abc-123", "site_id": "s1", "operator_id": "op1",
            "device_id": "dev1", "started_utc": "2026-05-26T01:00:00Z",
            "finalized_utc": "2026-05-26T01:05:00Z", "status": "complete",
            "closed_reason": "janitor", "camera_ids_active": [1, 4],
            "event_count": 5,
            "alert_stage_log": [{"stage": "PRELIMINARY", "signal": "first_event",
                                  "ts": "2026-05-26T01:00:00Z"}],
        },
        event_log=[
            {"kind": "face_intruder", "camera_id": 4, "name": "INTRUDER",
             "timestamp_utc": "2026-05-26T01:01:00Z"},
            {"kind": "sensor_alarm", "camera_id": 4, "zone_id": 1,
             "timestamp_utc": "2026-05-26T01:02:00Z"},
        ],
        face_count=2, plate_count=1, sensor_count=1,
    )
    assert pdf_path is not None
    assert pdf_path.exists()
    head = pdf_path.read_bytes()[:4]
    assert head == b"%PDF"


# ─── highlight clip from a post video ──────────────────────────────


def test_build_highlight_returns_none_for_missing_source(tmp_path):
    assert build_highlight(tmp_path / "no_such_file.mp4") is None
