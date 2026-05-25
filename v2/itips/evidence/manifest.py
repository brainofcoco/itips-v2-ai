"""Manifest construction and hashing.

The manifest is a JSON file that lists every artifact in the evidence
package with its SHA-256. The manifest itself is then SHA-256 hashed
to produce `manifest_hash`, which is the input to HMAC signing.

Determinism matters here. We sort entries by filename and write JSON
with sorted keys and a stable separator so two assemblies of the same
inputs produce byte-identical manifest files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ManifestEntry:
    filename: str
    sha256: str
    bytes: int
    kind: str  # 'metadata' | 'video' | 'face' | 'plate' | 'event_log' | ...


@dataclass
class Manifest:
    incident_id: str
    site_id: str
    operator_id: str
    device_id: str
    created_utc: str
    entries: list[ManifestEntry] = field(default_factory=list)

    def add(self, entry: ManifestEntry) -> None:
        self.entries.append(entry)

    def to_json(self) -> str:
        body = {
            "incident_id": self.incident_id,
            "site_id": self.site_id,
            "operator_id": self.operator_id,
            "device_id": self.device_id,
            "created_utc": self.created_utc,
            "files": [asdict(e) for e in sorted(self.entries, key=lambda e: e.filename)],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def write(self, path: Path) -> str:
        """Write the manifest to disk and return its SHA-256."""
        body = self.to_json()
        Path(path).write_text(body, encoding="utf-8")
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
