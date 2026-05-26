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

    # ─── ML fallback layer ────────────────────────────────────────────
    # All ML services are optional. CapabilityRouter is always safe to
    # build (pure-Python). Engines only initialise if the `ml` extras
    # are installed; if not, calls return immediately and the event
    # worker degrades to the bare native-only path. In a vanilla v2
    # deploy with all-native cameras, no model is loaded and no GPU
    # memory is reserved.
    ml_state = _build_ml_layer(
        embedding_db_path=personnel_store_path.parent / "face_embeddings.sqlite",
        zones_path=personnel_store_path.parent / "zones.json",
        overrides_path=personnel_store_path.parent / "ml_overrides.json",
    )

    # ─── Sensor pipeline ──────────────────────────────────────────────
    # Same posture as the ML layer: best-effort wiring. SensorMap is
    # always safe to build (it's just JSON-on-disk). The dispatcher
    # needs the alert engine + dahua manager, which we have here, and
    # *optionally* the FaceEngine for the validation branch. Without
    # the FaceEngine, dispatches still pan + snapshot + log — just
    # without the auto-recognise step.
    from itips.runtime.sensor_dispatcher import SensorDispatcher
    from itips.sensors.sensor_event import SensorEventTap
    from itips.sensors.sensor_map import SensorMap
    sensor_map = SensorMap(path=personnel_store_path.parent / "sensor_map.json")
    sensor_event_tap = SensorEventTap()
    sensor_dispatcher = SensorDispatcher(
        alert_engine=alert_engine,
        dahua_manager=dahua_manager,
        sensor_map=sensor_map,
        event_tap=sensor_event_tap,
        face_engine=ml_state.face_engine,
    )

    # AX PRO hub listener — only constructed when the operator has
    # supplied credentials AND hikaxpro is installed. Otherwise the
    # dispatcher still runs and the Simulate button still works.
    axpro_listener = _build_axpro_listener(sensor_dispatcher)

    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=frame_bus,
        recorders=recorders,
        event_tap=event_tap,
        capability_router=ml_state.router,
        face_engine=ml_state.face_engine,
        plate_engine=ml_state.plate_engine,
        behavior_engine=ml_state.behavior_engine,
    )

    public_api = PublicApiServer(
        frame_bus=frame_bus,
        alert_engine=alert_engine,
        dahua_manager=dahua_manager,
        personnel_store=personnel_store,
        event_tap=event_tap,
        capability_router=ml_state.router,
        face_engine=ml_state.face_engine,
        plate_engine=ml_state.plate_engine,
        behavior_engine=ml_state.behavior_engine,
        zone_store=ml_state.zone_store,
        sensor_map=sensor_map,
        sensor_dispatcher=sensor_dispatcher,
        sensor_event_tap=sensor_event_tap,
        axpro_listener=axpro_listener,
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
    if ml_state.embedding_store is not None:
        services.append(ml_state.embedding_store)
    services.append(sensor_dispatcher)
    if axpro_listener is not None:
        services.append(axpro_listener)
    return deps, services


def _build_axpro_listener(dispatcher):
    """Best-effort AX PRO hub listener construction.

    Returns `None` when:
      * `ITIPS_AXPRO_HOST` is unset / empty (operator hasn't wired a
        hub — that's fine, the Simulate button still works), OR
      * `hikaxpro` isn't installed in this build of the image, OR
      * any other unexpected failure during construction.

    The orchestrator's start-all loop will call `start()` on the
    listener if one is returned; the listener itself raises
    `AxProUnavailable` if the lib isn't importable, which we catch
    here too so a missing dep can't kill the boot sequence.
    """
    import os
    logger = logging.getLogger("itips.app.axpro")
    host = (os.environ.get("ITIPS_AXPRO_HOST") or "").strip()
    if not host:
        logger.info("AX PRO listener disabled (ITIPS_AXPRO_HOST not set) — "
                    "use the dashboard's Simulate button to drive the sensor pipeline")
        return None
    username = (os.environ.get("ITIPS_AXPRO_USERNAME") or "").strip()
    password = os.environ.get("ITIPS_AXPRO_PASSWORD") or ""
    if not username or not password:
        logger.warning(
            "AX PRO listener disabled — ITIPS_AXPRO_HOST is set but "
            "ITIPS_AXPRO_USERNAME / ITIPS_AXPRO_PASSWORD are not"
        )
        return None
    try:
        from itips.sensors.axpro_listener import AxProListener
    except Exception:
        logger.exception("AX PRO listener import failed")
        return None
    poll_ms = int(os.environ.get("ITIPS_AXPRO_POLL_MS", "500") or "500")
    return AxProListener(
        host=host, username=username, password=password,
        dispatcher=dispatcher,
        poll_interval_s=max(0.1, poll_ms / 1000.0),
    )


class _MlLayerState:
    """All the optional services `_build_ml_layer` produces."""

    def __init__(self) -> None:
        self.router = None
        self.face_engine = None
        self.embedding_store = None
        self.plate_engine = None
        self.behavior_engine = None
        self.object_detector = None
        self.zone_store = None


def _build_ml_layer(*, embedding_db_path, zones_path,
                    overrides_path) -> "_MlLayerState":
    """Best-effort ML wiring.

    Every individual engine is allowed to fail independently — if
    `ultralytics` is installed but `insightface` isn't, the behavior
    fallback works and the face fallback degrades. Failure here must
    never break the baseline runtime — log and continue.
    """
    logger = logging.getLogger("itips.app.ml")
    state = _MlLayerState()
    try:
        from itips.ml import (
            BehaviorEngine, CapabilityRouter, EmbeddingStore, FaceEngine,
            ObjectDetector, PlateEngine, ZoneStore,
        )
    except Exception:
        logger.warning("ml package import failed — running with no fallback layer")
        return state

    state.router = CapabilityRouter(overrides_path=overrides_path)

    # Face fallback — InsightFace SCRFD+ArcFace.
    try:
        state.embedding_store = EmbeddingStore(db_path=embedding_db_path)
        state.face_engine = FaceEngine(embedding_store=state.embedding_store)
        state.face_engine.warmup_async()  # lazy; never blocks boot
    except Exception:
        logger.exception("face fallback disabled (will degrade to bare bbox)")

    # ANPR fallback — EasyOCR.
    try:
        state.plate_engine = PlateEngine()
        state.plate_engine.warmup_async()
    except Exception:
        logger.exception("plate fallback disabled (will degrade to log-only)")

    # Behavior fallback — YOLOv8 + IoU tracker + zone polygons.
    try:
        state.zone_store = ZoneStore(path=zones_path)
        state.object_detector = ObjectDetector()
        state.behavior_engine = BehaviorEngine(
            zone_store=state.zone_store,
            object_detector=state.object_detector,
        )
        state.behavior_engine.warmup_async()
    except Exception:
        logger.exception("behavior fallback disabled (will degrade to motion-only)")

    return state
