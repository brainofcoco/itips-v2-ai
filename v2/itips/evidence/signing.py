"""SHA-256 file hashing + HMAC-SHA-256 package signature.

The HMAC key is per-device and never appears in this module — callers
pass it in. In production the key is loaded once at boot from
`ITIPS_DEVICE_HMAC_KEY` and held in memory for the process lifetime.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path


def compute_file_hash(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the SHA-256 hex digest of a file's contents.

    Streamed in 1 MiB chunks so we can hash multi-GB video files without
    loading them into memory.
    """
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sign_manifest(
    *,
    manifest_hash: str,
    device_id: str,
    site_id: str,
    incident_id: str,
    signing_timestamp_utc: str,
    hmac_key_hex: str,
) -> str:
    """Produce the package HMAC per PRD §4.3 REQ-EV-02.

    Signing input is the concatenation of:
        manifest_hash || device_id || site_id || incident_id || signing_timestamp_utc
    Each component is ASCII-encoded. The result is hex-encoded HMAC-SHA-256.

    A mismatch in any field at verification time invalidates the signature.
    """
    if not hmac_key_hex:
        raise ValueError("HMAC key is empty — refusing to sign without a device key")
    try:
        key = bytes.fromhex(hmac_key_hex)
    except ValueError as exc:
        raise ValueError("ITIPS_DEVICE_HMAC_KEY must be hex-encoded") from exc

    signing_input = (
        f"{manifest_hash}|{device_id}|{site_id}|{incident_id}|{signing_timestamp_utc}"
    ).encode("ascii")
    return hmac.new(key, signing_input, hashlib.sha256).hexdigest()
