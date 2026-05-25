import hashlib
import json
from pathlib import Path

from itips.evidence.manifest import Manifest, ManifestEntry


def _entry(name: str) -> ManifestEntry:
    return ManifestEntry(filename=name, sha256="0" * 64, bytes=1, kind="test")


def test_manifest_is_deterministic_under_reorder(tmp_path: Path):
    m1 = Manifest(incident_id="i", site_id="s", operator_id="o",
                  device_id="d", created_utc="2026-01-01T00:00:00Z")
    m1.add(_entry("z.bin"))
    m1.add(_entry("a.bin"))
    m1.add(_entry("m.bin"))
    p1 = tmp_path / "m1.json"
    h1 = m1.write(p1)

    m2 = Manifest(incident_id="i", site_id="s", operator_id="o",
                  device_id="d", created_utc="2026-01-01T00:00:00Z")
    m2.add(_entry("a.bin"))
    m2.add(_entry("m.bin"))
    m2.add(_entry("z.bin"))
    p2 = tmp_path / "m2.json"
    h2 = m2.write(p2)

    assert h1 == h2
    assert p1.read_text() == p2.read_text()


def test_manifest_hash_is_sha256_of_body(tmp_path: Path):
    m = Manifest(incident_id="i", site_id="s", operator_id="o",
                 device_id="d", created_utc="2026-01-01T00:00:00Z")
    m.add(_entry("a.bin"))
    p = tmp_path / "m.json"
    declared = m.write(p)
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert declared == expected


def test_manifest_files_are_sorted_in_output(tmp_path: Path):
    m = Manifest(incident_id="i", site_id="s", operator_id="o",
                 device_id="d", created_utc="2026-01-01T00:00:00Z")
    for name in ("z", "y", "a", "m"):
        m.add(_entry(f"{name}.bin"))
    body = json.loads(m.to_json())
    names = [f["filename"] for f in body["files"]]
    assert names == sorted(names)
