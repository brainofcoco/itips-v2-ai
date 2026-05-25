"""Per-site registry of Dahua client instances.

The inbound 8443 API, the dashboard 5050 API, and the event dispatcher
all need to talk to the same set of cameras with the same credentials.
Build the registry once at boot and inject it everywhere.

Group binding is idempotent — `ensure_workers_group()` runs on first
construction so the WizMind firmware emits `FaceRecognition` events
against our group rather than the no-op default.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

from itips.camera.dahua_deterrence import DahuaDeterrence
from itips.camera.dahua_face_db import DEFAULT_GROUP_NAME, DEFAULT_SIMILARITY, DahuaFaceDB
from itips.camera.dahua_http import DahuaCameraEndpoint, endpoints_from_settings
from itips.camera.dahua_plate_db import DahuaPlateDB
from itips.camera.dahua_ptz import DahuaPTZ

logger = logging.getLogger(__name__)


@dataclass
class CameraClients:
    camera_id: int
    endpoint: DahuaCameraEndpoint
    face_db: DahuaFaceDB
    plate_db: DahuaPlateDB
    deterrence: DahuaDeterrence
    ptz: DahuaPTZ
    workers_group_id: Optional[str] = None


class DahuaManager:
    """Single source of truth for Dahua clients across all surfaces."""

    def __init__(
        self,
        *,
        workers_group_name: str = DEFAULT_GROUP_NAME,
        face_similarity: int = DEFAULT_SIMILARITY,
    ) -> None:
        self._workers_group_name = workers_group_name
        self._face_similarity = face_similarity
        self._clients: dict[int, CameraClients] = {}
        self._lock = threading.Lock()
        self._initialise()

    # ─── construction ─────────────────────────────────────────────────

    def _initialise(self) -> None:
        endpoints = endpoints_from_settings()
        for cam_id, endpoint in endpoints.items():
            try:
                self._clients[cam_id] = self._build_one(cam_id, endpoint)
            except Exception:
                logger.exception("cam %d: client build failed for %s",
                                 cam_id, endpoint.safe_label())

    def _build_one(self, cam_id: int, endpoint: DahuaCameraEndpoint) -> CameraClients:
        clients = CameraClients(
            camera_id=cam_id,
            endpoint=endpoint,
            face_db=DahuaFaceDB(endpoint),
            plate_db=DahuaPlateDB(endpoint),
            deterrence=DahuaDeterrence(endpoint),
            ptz=DahuaPTZ(endpoint, camera_id=cam_id),
        )
        try:
            group_id = clients.face_db.ensure_workers_group(self._workers_group_name)
            clients.face_db.bind_group_to_channel(
                group_id, channel=1, similarity=self._face_similarity,
            )
            clients.workers_group_id = group_id
            logger.info(
                "cam %d: face group '%s' bound (groupID=%s, similarity=%d)",
                cam_id, self._workers_group_name, group_id, self._face_similarity,
            )
        except Exception:
            # Camera might not support faceRecognitionServer (firmware
            # variance). Log and continue — FaceDetection still fires and
            # AlertEngine can fall back to behaviour rules.
            logger.warning(
                "cam %d: faceRecognitionServer not available (%s) — falling back",
                cam_id, endpoint.safe_label(),
            )
        return clients

    # ─── lookups ──────────────────────────────────────────────────────

    def camera_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._clients.keys())

    def get(self, camera_id: int) -> Optional[CameraClients]:
        with self._lock:
            return self._clients.get(camera_id)

    def all(self) -> list[CameraClients]:
        with self._lock:
            return list(self._clients.values())

    # ─── service shim ─────────────────────────────────────────────────
    # The orchestrator's service list expects start()/stop().

    def start(self) -> None:
        # Construction is eager; nothing to do here.
        return

    def stop(self) -> None:
        return
