"""Per-incident evidence package assembly.

Public surface:
  - start_incident(...) → incident_id  (allocates the package dir)
  - attach_file(incident_id, path, kind)
  - attach_event(incident_id, event_dict)
  - finalize(incident_id) → Path  (writes manifest + signature, returns dir)

Internally the packager runs as a daemon thread that owns a queue of
operations so the camera workers never block on disk I/O. The thread
serialises writes per-incident — important because finalize must see
all attaches.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config.settings import settings
from itips.evidence.manifest import Manifest, ManifestEntry
from itips.evidence.signing import compute_file_hash, sign_manifest
from itips.utils.clock import now_iso

logger = logging.getLogger(__name__)


@dataclass
class _IncidentState:
    incident_id: str
    package_dir: Path
    site_id: str
    operator_id: str
    device_id: str
    event_log: list[dict[str, Any]] = field(default_factory=list)
    manifest: Manifest = None  # type: ignore[assignment]


@dataclass
class _StartOp:
    incident_id: str
    site_id: str
    operator_id: str
    device_id: str


@dataclass
class _AttachFile:
    incident_id: str
    path: Path
    kind: str


@dataclass
class _AttachEvent:
    incident_id: str
    event: dict[str, Any]


@dataclass
class _Finalize:
    incident_id: str
    done: threading.Event
    result: dict[str, Any] = field(default_factory=dict)


class EvidencePackager(threading.Thread):
    """Assembles, signs, and finalises evidence packages.

    The signing key is read once at construction so we can fail fast in
    deployments where the device key was not provisioned.
    """

    def __init__(self, store_root: Path, pre_event_seconds: int, post_event_seconds: int) -> None:
        super().__init__(name="evidence-packager", daemon=True)
        self.store_root = Path(store_root)
        self.pre_event_seconds = pre_event_seconds
        self.post_event_seconds = post_event_seconds
        self._ops: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._states: dict[str, _IncidentState] = {}

    # ─── public surface ────────────────────────────────────────────

    def start_incident(self, *, site_id: str, operator_id: str, device_id: str) -> str:
        """Allocate a fresh incident_id and submit the start op."""
        incident_id = str(uuid.uuid4())
        self._ops.put(_StartOp(incident_id, site_id, operator_id, device_id))
        return incident_id

    def attach_file(self, incident_id: str, path: Path, kind: str) -> None:
        self._ops.put(_AttachFile(incident_id, Path(path), kind))

    def attach_event(self, incident_id: str, event: dict[str, Any]) -> None:
        self._ops.put(_AttachEvent(incident_id, dict(event)))

    def finalize(self, incident_id: str, timeout: float = 30.0) -> Path:
        """Block until the package is signed and on disk."""
        op = _Finalize(incident_id, threading.Event())
        self._ops.put(op)
        if not op.done.wait(timeout):
            raise TimeoutError(f"Finalize timed out after {timeout}s for {incident_id}")
        result = op.result
        if result.get("status") != "ok":
            raise RuntimeError(f"Finalize failed: {result}")
        return Path(result["package_dir"])

    def stop(self) -> None:
        self._stop.set()

    # ─── thread body ───────────────────────────────────────────────

    def run(self) -> None:
        self.store_root.mkdir(parents=True, exist_ok=True)
        logger.info("EvidencePackager ready at %s", self.store_root)
        while not self._stop.is_set() or not self._ops.empty():
            try:
                op = self._ops.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._dispatch(op)
            except Exception:
                logger.exception("EvidencePackager: op failed: %s", op.__class__.__name__)
        logger.info("EvidencePackager stopped.")

    def _dispatch(self, op) -> None:
        if isinstance(op, _StartOp):
            self._handle_start(op)
        elif isinstance(op, _AttachFile):
            self._handle_attach_file(op)
        elif isinstance(op, _AttachEvent):
            self._handle_attach_event(op)
        elif isinstance(op, _Finalize):
            self._handle_finalize(op)

    def _handle_start(self, op: _StartOp) -> None:
        package_dir = self.store_root / "incidents" / op.incident_id
        (package_dir / "face_captures").mkdir(parents=True, exist_ok=True)
        (package_dir / "plate_captures").mkdir(parents=True, exist_ok=True)
        metadata = {
            "incident_id": op.incident_id,
            "site_id": op.site_id,
            "operator_id": op.operator_id,
            "device_id": op.device_id,
            "started_utc": now_iso(),
        }
        (package_dir / "incident_metadata.json").write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        state = _IncidentState(
            incident_id=op.incident_id,
            package_dir=package_dir,
            site_id=op.site_id,
            operator_id=op.operator_id,
            device_id=op.device_id,
            manifest=Manifest(
                incident_id=op.incident_id,
                site_id=op.site_id,
                operator_id=op.operator_id,
                device_id=op.device_id,
                created_utc=metadata["started_utc"],
            ),
        )
        self._states[op.incident_id] = state
        logger.info("Incident %s opened at %s", op.incident_id, package_dir)

    def _handle_attach_file(self, op: _AttachFile) -> None:
        state = self._states.get(op.incident_id)
        if not state or not op.path.exists():
            return
        digest = compute_file_hash(op.path)
        rel = op.path.relative_to(state.package_dir) if op.path.is_relative_to(state.package_dir) else op.path.name
        state.manifest.add(ManifestEntry(
            filename=str(rel),
            sha256=digest,
            bytes=op.path.stat().st_size,
            kind=op.kind,
        ))

    def _handle_attach_event(self, op: _AttachEvent) -> None:
        state = self._states.get(op.incident_id)
        if not state:
            return
        state.event_log.append(op.event)

    def _handle_finalize(self, op: _Finalize) -> None:
        state = self._states.pop(op.incident_id, None)
        if not state:
            op.result.update(status="error", reason="unknown incident")
            op.done.set()
            return
        try:
            event_log_path = state.package_dir / "event_log.json"
            event_log_path.write_text(
                json.dumps(state.event_log, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            state.manifest.add(ManifestEntry(
                filename="event_log.json",
                sha256=compute_file_hash(event_log_path),
                bytes=event_log_path.stat().st_size,
                kind="event_log",
            ))
            state.manifest.add(ManifestEntry(
                filename="incident_metadata.json",
                sha256=compute_file_hash(state.package_dir / "incident_metadata.json"),
                bytes=(state.package_dir / "incident_metadata.json").stat().st_size,
                kind="metadata",
            ))

            manifest_path = state.package_dir / "manifest.json"
            manifest_hash = state.manifest.write(manifest_path)
            signing_ts = now_iso()
            signature = sign_manifest(
                manifest_hash=manifest_hash,
                device_id=state.device_id,
                site_id=state.site_id,
                incident_id=state.incident_id,
                signing_timestamp_utc=signing_ts,
                hmac_key_hex=settings.evidence.hmac_key_hex,
            )
            signature_body = {
                "algorithm": "HMAC-SHA-256",
                "manifest_hash": manifest_hash,
                "signing_timestamp_utc": signing_ts,
                "device_id": state.device_id,
                "site_id": state.site_id,
                "incident_id": state.incident_id,
                "signature": signature,
            }
            (state.package_dir / "signature.sha256").write_text(
                json.dumps(signature_body, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            op.result.update(status="ok", package_dir=str(state.package_dir),
                             signature=signature, manifest_hash=manifest_hash)
            logger.info("Incident %s finalised; signature=%s", state.incident_id, signature[:16])
        except Exception as exc:
            logger.exception("Finalize failed for %s", op.incident_id)
            op.result.update(status="error", reason=str(exc))
        finally:
            op.done.set()
