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
from itips.evidence.summary import build_highlight, build_pdf_summary
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
    # Filled progressively from attach calls; written to incident_metadata.json
    # at finalize to satisfy PRD §4.3 REQ-EV-01.
    metadata_patch: dict[str, Any] = field(default_factory=dict)
    camera_ids_active: set[int] = field(default_factory=set)
    alert_stage_log: list[dict[str, Any]] = field(default_factory=list)
    face_seq: int = 0
    plate_seq: int = 0
    sensor_seq: int = 0


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
class _AttachFaceCapture:
    incident_id: str
    jpeg: bytes
    confidence: float
    name: str
    ts: str


@dataclass
class _AttachPlateCapture:
    incident_id: str
    jpeg: bytes
    plate_number: str
    confidence: float
    ts: str


@dataclass
class _AttachSensorCapture:
    """A still attached by a sensor itself (e.g. AX PRO PIR-cam JPEG)."""
    incident_id: str
    jpeg: bytes
    source: str          # e.g. "axpro_pircam"
    zone_id: int
    zone_name: str
    ts: str


@dataclass
class _UpdateMetadata:
    incident_id: str
    patch: dict[str, Any]


@dataclass
class _Finalize:
    incident_id: str
    done: threading.Event
    closed_reason: str = "idle_timeout"
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
        self._stop_event = threading.Event()
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

    def attach_face_capture(self, incident_id: str, *, jpeg: bytes,
                            confidence: float = 0.0, name: str = "",
                            ts: Optional[str] = None) -> None:
        """Persist a face crop to face_captures/face_NN_<ts>_<conf>.jpg."""
        self._ops.put(_AttachFaceCapture(
            incident_id=incident_id, jpeg=jpeg,
            confidence=float(confidence), name=name,
            ts=ts or now_iso(),
        ))

    def attach_plate_capture(self, incident_id: str, *, jpeg: bytes,
                             plate_number: str = "", confidence: float = 0.0,
                             ts: Optional[str] = None) -> None:
        """Persist a plate crop to plate_captures/plate_NN_<ts>_<plate>.jpg."""
        self._ops.put(_AttachPlateCapture(
            incident_id=incident_id, jpeg=jpeg,
            plate_number=plate_number, confidence=float(confidence),
            ts=ts or now_iso(),
        ))

    def attach_sensor_capture(self, incident_id: str, *, jpeg: bytes,
                              source: str, zone_id: int,
                              zone_name: str = "",
                              ts: Optional[str] = None) -> None:
        """Persist an image attached by the sensor itself (PIR-cam JPEG)
        to sensor_captures/<source>_zone<N>_<ts>.jpg."""
        self._ops.put(_AttachSensorCapture(
            incident_id=incident_id, jpeg=jpeg,
            source=source, zone_id=int(zone_id),
            zone_name=zone_name, ts=ts or now_iso(),
        ))

    def update_metadata(self, incident_id: str, patch: dict[str, Any]) -> None:
        """Merge fields into incident_metadata.json at finalize.

        Use for camera_ids_active, incident_classification, gps_coordinates,
        responder_dispatch_log, etc. — fields PRD §4.3 REQ-EV-01 mandates
        but only become known partway through the incident lifecycle.
        """
        self._ops.put(_UpdateMetadata(incident_id, dict(patch)))

    def finalize(self, incident_id: str, *, closed_reason: str = "idle_timeout",
                 timeout: float = 30.0) -> Path:
        """Block until the package is signed and on disk."""
        op = _Finalize(incident_id, threading.Event(), closed_reason=closed_reason)
        self._ops.put(op)
        if not op.done.wait(timeout):
            raise TimeoutError(f"Finalize timed out after {timeout}s for {incident_id}")
        result = op.result
        if result.get("status") != "ok":
            raise RuntimeError(f"Finalize failed: {result}")
        return Path(result["package_dir"])

    def stop(self) -> None:
        self._stop_event.set()

    def _sweep_orphans(self) -> None:
        """Delete incident dirs that were opened but never finalized.

        An incident only writes its manifest + signature at finalize. If
        the process died/restarted while an incident was still in the
        PRELIMINARY stage, the on-disk directory is left with just the
        initial metadata — and because its in-memory state is gone it can
        never be finalized. Those orphans show up on the dashboard as
        empty incidents (blank manifest/signature, no events) and only
        accumulate. We run at startup, before any new incident exists in
        this process, so every manifest-less directory here is a stale
        orphan from a previous run and safe to remove.
        """
        import shutil
        incidents_root = self.store_root / "incidents"
        if not incidents_root.is_dir():
            return
        removed = 0
        for d in incidents_root.iterdir():
            if not d.is_dir():
                continue
            if (d / "manifest.json").exists():
                continue   # finalized — keep
            try:
                shutil.rmtree(d)
                removed += 1
            except OSError:
                logger.exception("orphan sweep: failed to remove %s", d.name)
        if removed:
            logger.info(
                "EvidencePackager: swept %d orphaned (never-finalized) "
                "incident package(s) at startup", removed,
            )

    # ─── thread body ───────────────────────────────────────────────

    def run(self) -> None:
        self.store_root.mkdir(parents=True, exist_ok=True)
        self._sweep_orphans()
        logger.info("EvidencePackager ready at %s", self.store_root)
        while not self._stop_event.is_set() or not self._ops.empty():
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
        elif isinstance(op, _AttachFaceCapture):
            self._handle_attach_face(op)
        elif isinstance(op, _AttachPlateCapture):
            self._handle_attach_plate(op)
        elif isinstance(op, _AttachSensorCapture):
            self._handle_attach_sensor(op)
        elif isinstance(op, _UpdateMetadata):
            self._handle_update_metadata(op)
        elif isinstance(op, _Finalize):
            self._handle_finalize(op)

    def _handle_start(self, op: _StartOp) -> None:
        package_dir = self.store_root / "incidents" / op.incident_id
        (package_dir / "face_captures").mkdir(parents=True, exist_ok=True)
        (package_dir / "plate_captures").mkdir(parents=True, exist_ok=True)
        (package_dir / "sensor_captures").mkdir(parents=True, exist_ok=True)
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
        # Pick up evidence-shaping signals as side-effects of normal events.
        cam = op.event.get("camera_id")
        if isinstance(cam, int) and cam > 0:
            state.camera_ids_active.add(cam)
        if op.event.get("kind") == "stage_change":
            state.alert_stage_log.append({
                "stage": op.event.get("stage"),
                "signal": op.event.get("signal"),
                "ts": op.event.get("timestamp_utc"),
            })

    def _handle_attach_face(self, op: _AttachFaceCapture) -> None:
        state = self._states.get(op.incident_id)
        if not state:
            return
        state.face_seq += 1
        safe_ts = _safe_filename_ts(op.ts)
        fname = f"face_{state.face_seq:03d}_{safe_ts}_conf{int(op.confidence * 100):02d}.jpg"
        path = state.package_dir / "face_captures" / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(op.jpeg)
        digest = compute_file_hash(path)
        state.manifest.add(ManifestEntry(
            filename=f"face_captures/{fname}",
            sha256=digest,
            bytes=path.stat().st_size,
            kind="face_capture",
        ))
        # Log so face captures appear in event_log too — gives investigators
        # a single chronological view.
        state.event_log.append({
            "kind": "face_capture",
            "filename": f"face_captures/{fname}",
            "confidence": op.confidence,
            "name": op.name,
            "timestamp_utc": op.ts,
        })

    def _handle_attach_sensor(self, op: _AttachSensorCapture) -> None:
        state = self._states.get(op.incident_id)
        if not state:
            return
        state.sensor_seq += 1
        safe_ts = _safe_filename_ts(op.ts)
        safe_source = "".join(c if c.isalnum() else "_" for c in op.source) or "sensor"
        fname = f"{safe_source}_zone{op.zone_id:02d}_{state.sensor_seq:03d}_{safe_ts}.jpg"
        path = state.package_dir / "sensor_captures" / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(op.jpeg)
        digest = compute_file_hash(path)
        state.manifest.add(ManifestEntry(
            filename=f"sensor_captures/{fname}",
            sha256=digest,
            bytes=path.stat().st_size,
            kind="sensor_capture",
        ))
        state.event_log.append({
            "kind": "sensor_capture",
            "filename": f"sensor_captures/{fname}",
            "source": op.source,
            "zone_id": op.zone_id,
            "zone_name": op.zone_name,
            "timestamp_utc": op.ts,
        })

    def _handle_attach_plate(self, op: _AttachPlateCapture) -> None:
        state = self._states.get(op.incident_id)
        if not state:
            return
        state.plate_seq += 1
        safe_ts = _safe_filename_ts(op.ts)
        safe_plate = "".join(c for c in op.plate_number if c.isalnum()) or "unknown"
        fname = f"plate_{state.plate_seq:03d}_{safe_ts}_{safe_plate}.jpg"
        path = state.package_dir / "plate_captures" / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(op.jpeg)
        digest = compute_file_hash(path)
        state.manifest.add(ManifestEntry(
            filename=f"plate_captures/{fname}",
            sha256=digest,
            bytes=path.stat().st_size,
            kind="plate_capture",
        ))
        state.event_log.append({
            "kind": "plate_capture",
            "filename": f"plate_captures/{fname}",
            "plate_number": op.plate_number,
            "confidence": op.confidence,
            "timestamp_utc": op.ts,
        })

    def _handle_update_metadata(self, op: _UpdateMetadata) -> None:
        state = self._states.get(op.incident_id)
        if not state:
            return
        state.metadata_patch.update(op.patch)

    def _handle_finalize(self, op: _Finalize) -> None:
        state = self._states.pop(op.incident_id, None)
        if not state:
            op.result.update(status="error", reason="unknown incident")
            op.done.set()
            return
        try:
            # Split sensor events into their own log per PRD §4.3.
            sensor_log = [e for e in state.event_log if _is_sensor_event(e)]
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
            if sensor_log:
                sensor_log_path = state.package_dir / "sensor_log.json"
                sensor_log_path.write_text(
                    json.dumps(sensor_log, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                state.manifest.add(ManifestEntry(
                    filename="sensor_log.json",
                    sha256=compute_file_hash(sensor_log_path),
                    bytes=sensor_log_path.stat().st_size,
                    kind="sensor_log",
                ))

            # Rewrite incident_metadata.json with chain-of-custody + status.
            metadata_path = state.package_dir / "incident_metadata.json"
            existing = {}
            try:
                existing = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            final_metadata = {
                **existing,
                **state.metadata_patch,
                "camera_ids_active": sorted(state.camera_ids_active),
                "alert_stage_log": list(state.alert_stage_log),
                "event_count": len(state.event_log),
                "sensor_event_count": len(sensor_log),
                "face_capture_count": state.face_seq,
                "plate_capture_count": state.plate_seq,
                "status": "complete",
                "closed_reason": op.closed_reason,
                "finalized_utc": now_iso(),
            }
            metadata_path.write_text(
                json.dumps(final_metadata, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            state.manifest.add(ManifestEntry(
                filename="incident_metadata.json",
                sha256=compute_file_hash(metadata_path),
                bytes=metadata_path.stat().st_size,
                kind="metadata",
            ))

            # Highlight clips — first 30s of each video_post file.
            for entry in list(state.manifest.entries):
                if entry.kind != "video_post":
                    continue
                src = state.package_dir / entry.filename
                hl = build_highlight(src, duration_s=30.0)
                if hl is not None and hl.exists():
                    rel = hl.relative_to(state.package_dir)
                    state.manifest.add(ManifestEntry(
                        filename=str(rel),
                        sha256=compute_file_hash(hl),
                        bytes=hl.stat().st_size,
                        kind="video_highlight",
                    ))

            # Human-readable summary PDF for law enforcement.
            pdf = build_pdf_summary(
                package_dir=state.package_dir,
                metadata=final_metadata,
                event_log=state.event_log,
                face_count=state.face_seq,
                plate_count=state.plate_seq,
                sensor_count=len(sensor_log),
            )
            if pdf is not None and pdf.exists():
                state.manifest.add(ManifestEntry(
                    filename="incident_summary.pdf",
                    sha256=compute_file_hash(pdf),
                    bytes=pdf.stat().st_size,
                    kind="summary_pdf",
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


_SENSOR_EVENT_KINDS = {
    "sensor_alarm", "sensor_unverified", "sensor_intruder",
}


def _is_sensor_event(event: dict[str, Any]) -> bool:
    kind = event.get("kind") or event.get("alert_type") or ""
    return str(kind) in _SENSOR_EVENT_KINDS


def _safe_filename_ts(ts: str) -> str:
    # ISO timestamps contain ":" which Windows refuses + most tools dislike.
    return "".join(c if c.isalnum() else "-" for c in ts)[:24]
