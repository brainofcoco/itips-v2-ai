"""End-to-end test for an incident package — start, attach, finalize, verify."""

import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

from itips.evidence.packager import EvidencePackager


@pytest.fixture
def packager(tmp_evidence_root):
    p = EvidencePackager(
        store_root=tmp_evidence_root,
        pre_event_seconds=10,
        post_event_seconds=10,
    )
    p.start()
    yield p
    p.stop()
    p.join(timeout=2)


def test_package_contains_signed_manifest(packager, tmp_path):
    incident_id = packager.start_incident(
        site_id="site-test", operator_id="op-test", device_id="jtx-test"
    )

    artifact = tmp_path / "face.jpg"
    artifact.write_bytes(b"\xff\xd8\xff\xe0face")
    packager.attach_file(incident_id, artifact, kind="face")
    packager.attach_event(incident_id, {"alert": "intrusion", "track_id": 1})

    package_dir = packager.finalize(incident_id)

    assert package_dir.exists()
    assert (package_dir / "manifest.json").exists()
    assert (package_dir / "signature.sha256").exists()
    assert (package_dir / "incident_metadata.json").exists()
    assert (package_dir / "event_log.json").exists()

    manifest = json.loads((package_dir / "manifest.json").read_text())
    files = {f["filename"] for f in manifest["files"]}
    assert "incident_metadata.json" in files
    assert "event_log.json" in files

    sig = json.loads((package_dir / "signature.sha256").read_text())
    assert sig["algorithm"] == "HMAC-SHA-256"
    assert sig["incident_id"] == incident_id

    # Independently recompute the signature using the same recipe.
    manifest_bytes = (package_dir / "manifest.json").read_bytes()
    recomputed_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    assert sig["manifest_hash"] == recomputed_manifest_hash

    signing_input = (
        f"{recomputed_manifest_hash}|{sig['device_id']}|{sig['site_id']}|"
        f"{sig['incident_id']}|{sig['signing_timestamp_utc']}"
    ).encode("ascii")
    expected_sig = hmac.new(
        bytes.fromhex(os.environ["ITIPS_DEVICE_HMAC_KEY"]),
        signing_input,
        hashlib.sha256,
    ).hexdigest()
    assert sig["signature"] == expected_sig
