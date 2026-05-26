"""Dashboard test routes — /api/evidence/test/run + verify + OpenAI scenario."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from itips.api.dashboard import register_dashboard
from itips.evidence.packager import EvidencePackager


@pytest.fixture
def packager(tmp_path, monkeypatch):
    monkeypatch.setenv("ITIPS_DEVICE_HMAC_KEY", "deadbeef" * 8)
    # settings.evidence is a frozen dataclass — bypass with __setattr__
    # so _safe_incident_dir finds packages we create in tmp.
    from config.settings import settings
    original = settings.evidence.store_path
    object.__setattr__(settings.evidence, "store_path", tmp_path / "ev")
    p = EvidencePackager(store_root=tmp_path / "ev",
                          pre_event_seconds=10, post_event_seconds=10)
    p.start()
    yield p
    p.stop(); p.join(timeout=2.0)
    object.__setattr__(settings.evidence, "store_path", original)


@pytest.fixture
def client(packager):
    class _Engine:
        def __init__(self, p): self._packager = p
        def history(self): return []
    app = Flask("test")
    register_dashboard(
        app, frame_bus=MagicMock(),
        dahua_manager=MagicMock(all=MagicMock(return_value=[]),
                                  camera_ids=MagicMock(return_value=[])),
        personnel_store=MagicMock(list_all=MagicMock(return_value=[])),
        alert_engine=_Engine(packager),
    )
    return app.test_client()


# ─── evidence test run + inspect + verify ──────────────────────────


def test_evidence_test_run_builds_complete_package(client):
    r = client.post("/api/evidence/test/run")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["incident_id"]
    assert body["signature"]
    assert body["manifest_hash"]


def test_get_incident_surfaces_thumbnails_and_pdf_flag(client):
    incident_id = client.post("/api/evidence/test/run").get_json()["incident_id"]
    r = client.get(f"/api/incidents/{incident_id}")
    assert r.status_code == 200
    body = r.get_json()
    # PRD artefacts surfaced.
    assert body["has_pdf"] is True
    assert len(body["face_captures"]) == 1
    assert len(body["plate_captures"]) == 1
    assert "summary_pdf" in body["manifest_kinds"]
    assert "face_capture" in body["manifest_kinds"]
    assert "plate_capture" in body["manifest_kinds"]
    assert "sensor_log" in body["manifest_kinds"]


def test_verify_incident_returns_all_ok_for_a_fresh_package(client):
    incident_id = client.post("/api/evidence/test/run").get_json()["incident_id"]
    r = client.post(f"/api/incidents/{incident_id}/verify")
    body = r.get_json()
    assert body["ok"] is True
    assert body["manifest_hash_ok"] is True
    assert all(f["status"] == "ok" for f in body["files"])


def test_verify_incident_detects_tampering(client, packager):
    incident_id = client.post("/api/evidence/test/run").get_json()["incident_id"]
    # Corrupt one of the face JPEGs.
    pkg = Path(packager.store_root) / "incidents" / incident_id
    faces = list((pkg / "face_captures").iterdir())
    faces[0].write_bytes(b"TAMPERED")
    r = client.post(f"/api/incidents/{incident_id}/verify")
    body = r.get_json()
    assert body["ok"] is False
    bad = [f for f in body["files"] if f["status"] != "ok"]
    assert len(bad) == 1
    assert bad[0]["filename"].startswith("face_captures/")


def test_file_download_attachment_vs_inline(client):
    incident_id = client.post("/api/evidence/test/run").get_json()["incident_id"]
    # Default = attachment.
    r = client.get(f"/api/incidents/{incident_id}/files/incident_summary.pdf")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    # ?inline=1 = inline.
    r = client.get(f"/api/incidents/{incident_id}/files/incident_summary.pdf?inline=1")
    assert r.status_code == 200
    assert "attachment" not in r.headers.get("Content-Disposition", "")


# ─── openai validate route gating (no real openai needed) ──────────


def test_openai_validate_route_503_when_disabled(client):
    r = client.post("/api/ml/openai/validate/behavior_intrusion",
                     data={}, content_type="multipart/form-data")
    assert r.status_code == 503
    assert "not enabled" in r.get_json()["error"]


def test_openai_validate_route_unknown_scenario_returns_400(packager):
    """With a wired validator, an unknown scenario returns 400."""
    class _Engine:
        def __init__(self, p): self._packager = p
        def history(self): return []
    fake_validator = MagicMock()
    fake_validator.is_enabled.return_value = True
    fake_validator.scenarios = ["behavior_intrusion", "face_intruder"]
    app = Flask("test")
    register_dashboard(
        app, frame_bus=MagicMock(),
        dahua_manager=MagicMock(all=MagicMock(return_value=[]),
                                  camera_ids=MagicMock(return_value=[])),
        personnel_store=MagicMock(list_all=MagicMock(return_value=[])),
        alert_engine=_Engine(packager),
        openai_validator=fake_validator,
    )
    c = app.test_client()
    r = c.post("/api/ml/openai/validate/not_a_scenario",
                data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    body = r.get_json()
    assert "behavior_intrusion" in body["known"]


def test_openai_validate_route_dispatches_to_validator(packager):
    """Happy path: real image, scenario exists → validator.validate called."""
    import base64
    import numpy as np
    import cv2
    from itips.ml.openai_validator import ValidationResult
    class _Engine:
        def __init__(self, p): self._packager = p
        def history(self): return []
    fake_validator = MagicMock()
    fake_validator.is_enabled.return_value = True
    fake_validator.scenarios = ["behavior_intrusion"]
    fake_validator.validate.return_value = ValidationResult(
        scenario="behavior_intrusion", verdict="false_positive",
        category="animal", confidence=0.91,
        summary="Goat crossed the perimeter zone",
        model="gpt-4o-mini", tokens_used=120,
    )
    app = Flask("test")
    register_dashboard(
        app, frame_bus=MagicMock(),
        dahua_manager=MagicMock(all=MagicMock(return_value=[]),
                                  camera_ids=MagicMock(return_value=[])),
        personnel_store=MagicMock(list_all=MagicMock(return_value=[])),
        alert_engine=_Engine(packager),
        openai_validator=fake_validator,
    )
    c = app.test_client()
    # Build a tiny real JPEG so _decode_upload works.
    frame = np.zeros((40, 40, 3), dtype="uint8")
    ok, buf = cv2.imencode(".jpg", frame)
    import io
    r = c.post("/api/ml/openai/validate/behavior_intrusion",
                data={"image": (io.BytesIO(buf.tobytes()), "frame.jpg"),
                      "camera_id": "4", "zone_name": "perimeter"},
                content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["verdict"] == "false_positive"
    assert body["category"] == "animal"
    assert body["should_suppress"] is True
    fake_validator.validate.assert_called_once()
    ctx = fake_validator.validate.call_args.args[2]
    assert ctx["camera_id"] == "4"
    assert ctx["zone_name"] == "perimeter"
