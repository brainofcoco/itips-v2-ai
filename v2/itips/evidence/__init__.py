"""Per-incident evidence assembly + HMAC-SHA-256 signing on the Jetson.

The package format conforms to PRD §4.3 (REQ-EV-01) and the signing
protocol conforms to REQ-EV-02. Signing happens on the edge, not in the
cloud — the cloud receives already-signed packages.
"""

from .manifest import Manifest, ManifestEntry
from .packager import EvidencePackager
from .signing import compute_file_hash, sign_manifest

__all__ = [
    "EvidencePackager",
    "Manifest",
    "ManifestEntry",
    "compute_file_hash",
    "sign_manifest",
]
