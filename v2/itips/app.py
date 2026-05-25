"""Bootstrap — wires components together and hands them to the orchestrator.

Read top-to-bottom. The order matters: settings → logging → Dahua manager
→ intake → packager + recorders → AlertEngine → APIs → event dispatchers.

The Jetson does **no inference**. All detection runs on Dahua cameras.
This module just builds the message pipes that turn camera events into
signed evidence packages and intake records for the Sync Agent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import settings
from itips.runtime import EventTap, FrameBus, Orchestrator
from itips.utils.logging import configure as configure_logging


def run() -> int:
    configure_logging(log_file=Path("/opt/itips/var/logs/itips.log")
                      if settings.mode == "prod" else None)
    logger = logging.getLogger("itips.app")
    logger.info("ITIPS V2 (Dahua-native) starting — mode=%s site=%s device=%s",
                settings.mode, settings.tenant.site_id or "(unset)",
                settings.tenant.device_id or "(unset)")

    orchestrator = Orchestrator(deps_factory=_build_deps)
    return orchestrator.run()


def _build_deps():
    """Construct every long-lived component and return (WorkerDeps, services).

    Single place where components meet each other. Keep flat — every wiring
    decision should be visible in one screen.
    """
    from itips.alerts.engine import AlertEngine
    from itips.api.inbound import InboundApiServer
    from itips.api.personnel_store import PersonnelStore
    from itips.api.public import PublicApiServer
    from itips.camera.dahua_manager import DahuaManager
    from itips.evidence.packager import EvidencePackager
    from itips.evidence.recorder import IncidentRecorder
    from itips.runtime.event_worker import WorkerDeps
    from itips.sync.intake import IntakeWriter

    intake = IntakeWriter(db_path=settings.intake.db_path)

    dahua_manager = DahuaManager()

    personnel_store_path = settings.intake.db_path.parent / "personnel.sqlite"
    personnel_store = PersonnelStore(db_path=personnel_store_path)

    evidence_packager = EvidencePackager(
        store_root=settings.evidence.store_path,
        pre_event_seconds=settings.evidence.pre_event_seconds,
        post_event_seconds=settings.evidence.post_event_seconds,
    )

    recorders = {
        cam_id: IncidentRecorder(
            camera_id=cam_id,
            packager=evidence_packager,
            pre_event_seconds=settings.evidence.pre_event_seconds,
            post_event_seconds=settings.evidence.post_event_seconds,
        )
        for cam_id in dahua_manager.camera_ids()
    }

    alert_engine = AlertEngine(
        intake=intake,
        evidence_packager=evidence_packager,
        tenant=settings.tenant,
        recorders=recorders,
        confirmation_dwell_seconds=settings.incident.confirmation_dwell_seconds,
        confirmation_window_seconds=settings.incident.confirmation_window_seconds,
        idle_timeout_seconds=settings.incident.idle_timeout_seconds,
    )

    frame_bus = FrameBus()
    event_tap = EventTap()

    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=frame_bus,
        recorders=recorders,
        event_tap=event_tap,
    )

    public_api = PublicApiServer(
        frame_bus=frame_bus,
        alert_engine=alert_engine,
        dahua_manager=dahua_manager,
        personnel_store=personnel_store,
        event_tap=event_tap,
    )
    inbound_api = InboundApiServer(
        dahua_manager=dahua_manager,
        personnel_store=personnel_store,
    )

    services = [
        intake,
        personnel_store,
        dahua_manager,
        evidence_packager,
        alert_engine,
        public_api,
        inbound_api,
    ]
    return deps, services
