import hashlib
import hmac
from pathlib import Path

import pytest

from itips.evidence.signing import compute_file_hash, sign_manifest


def test_file_hash_matches_hashlib(tmp_path: Path):
    blob = b"hello world\n" * 1000
    p = tmp_path / "blob"
    p.write_bytes(blob)
    assert compute_file_hash(p) == hashlib.sha256(blob).hexdigest()


def test_sign_manifest_is_deterministic():
    args = {
        "manifest_hash": "a" * 64,
        "device_id": "jtx-0001",
        "site_id": "site-001",
        "incident_id": "incident-x",
        "signing_timestamp_utc": "2026-05-25T10:00:00.000000Z",
        "hmac_key_hex": "b" * 64,
    }
    s1 = sign_manifest(**args)
    s2 = sign_manifest(**args)
    assert s1 == s2


def test_sign_manifest_matches_hmac():
    key_hex = "b" * 64
    args = {
        "manifest_hash": "a" * 64,
        "device_id": "jtx-0001",
        "site_id": "site-001",
        "incident_id": "incident-x",
        "signing_timestamp_utc": "2026-05-25T10:00:00.000000Z",
        "hmac_key_hex": key_hex,
    }
    actual = sign_manifest(**args)

    signing_input = (
        f"{args['manifest_hash']}|{args['device_id']}|{args['site_id']}|"
        f"{args['incident_id']}|{args['signing_timestamp_utc']}"
    ).encode("ascii")
    expected = hmac.new(bytes.fromhex(key_hex), signing_input, hashlib.sha256).hexdigest()
    assert actual == expected


def test_sign_manifest_rejects_missing_key():
    with pytest.raises(ValueError):
        sign_manifest(
            manifest_hash="a", device_id="d", site_id="s",
            incident_id="i", signing_timestamp_utc="t",
            hmac_key_hex="",
        )


def test_sign_manifest_rejects_non_hex_key():
    with pytest.raises(ValueError):
        sign_manifest(
            manifest_hash="a", device_id="d", site_id="s",
            incident_id="i", signing_timestamp_utc="t",
            hmac_key_hex="not-hex-at-all",
        )


def test_changing_any_field_changes_signature():
    base = {
        "manifest_hash": "a" * 64,
        "device_id": "jtx-0001",
        "site_id": "site-001",
        "incident_id": "incident-x",
        "signing_timestamp_utc": "2026-05-25T10:00:00Z",
        "hmac_key_hex": "b" * 64,
    }
    baseline = sign_manifest(**base)
    for field in ("manifest_hash", "device_id", "site_id", "incident_id", "signing_timestamp_utc"):
        mutated = {**base, field: base[field] + "x"}
        assert sign_manifest(**mutated) != baseline, f"Signature unchanged when {field} changed"
