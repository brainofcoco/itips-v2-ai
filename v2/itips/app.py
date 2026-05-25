"""Bootstrap — wires components together and hands them to the orchestrator.

Read top-to-bottom. The order matters: settings → logging → CUDA → intake →
detection engines → alert engine → workers + services.

Anything that needs to be replaced for tests (engines, intake, signing key)
can be swapped by editing this file; the rest of the codebase never depends
on concrete constructors.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import settings
from itips.runtime import Orchestrator, verify_cuda
from itips.runtime.frame_bus import FrameBus
from itips.utils.logging import configure as configure_logging


def run() -> int:
    configure_logging(log_file=Path("/opt/itips/var/logs/itips.log")
                      if settings.mode == "prod" else None)
    logger = logging.getLogger("itips.app")
    logger.info("ITIPS V2 starting — mode=%s site=%s device=%s",
                settings.mode, settings.tenant.site_id or "(unset)",
                settings.tenant.device_id or "(unset)")

    device = verify_cuda()
    logger.info("Inference device: %s", device)

    orchestrator = Orchestrator(deps_factory=_build_deps)
    return orchestrator.run()


def _build_deps():
    """Construct every long-lived component and return (WorkerDeps, services).

    This is the single place where components meet each other. Keep it
    flat — every wiring decision should be visible in one screen.
    """
    from itips.alerts.engine import AlertEngine
    from itips.alerts.ptz import PTZController
    from itips.api.inbound import InboundApiServer
    from itips.api.public import PublicApiServer
    from itips.behaviour.analyser import BehaviourAnalyser
    from itips.behaviour.zones import init_store as init_zone_store
    from itips.detection.face import FaceRecognitionEngine
    from itips.detection.face_authorizer import FaceAuthorizer
    from itips.detection.plate import PlateRecognizerClient
    from itips.detection.yolo import YOLOEngine
    from itips.evidence.packager import EvidencePackager
    from itips.evidence.recorder import IncidentRecorder
    from itips.runtime.camera_worker import WorkerDeps
    from itips.runtime.presets import PresetRegistry
    from itips.sensors.ax_pro import AXProListener
    from itips.sync.intake import IntakeWriter

    init_zone_store(seed_path=settings.zones.seed_path,
                    runtime_path=settings.zones.runtime_path)
    preset_registry = PresetRegistry(config_path=settings.zones.presets_path)

    intake = IntakeWriter(db_path=settings.intake.db_path)

    # Pick YOLO backend. Default is ultralytics (PyTorch under the hood,
    # ByteTrack tracking). On Jetson Orin Nano set ITIPS_YOLO_BACKEND=onnx
    # to swap in OnnxYOLOEngine and save ~2 GB of CUDA memory.
    # See docs/jetson-memory.md for context.
    import os as _os
    yolo_backend = _os.getenv("ITIPS_YOLO_BACKEND", "ultralytics").strip().lower()
    if yolo_backend == "onnx":
        from itips.detection.yolo_onnx import OnnxYOLOEngine
        yolo = OnnxYOLOEngine(
            model_path=settings.detection.yolo_model,
            fallback_model=settings.detection.yolo_fallback,
            img_size=settings.detection.yolo_img_size,
            confidence=settings.detection.yolo_confidence,
            iou=settings.detection.yolo_iou,
        )
    else:
        yolo = YOLOEngine(
            model_path=settings.detection.yolo_model,
            fallback_model=settings.detection.yolo_fallback,
            img_size=settings.detection.yolo_img_size,
            confidence=settings.detection.yolo_confidence,
            iou=settings.detection.yolo_iou,
        )
    # TEMP shrink for 8 GB Orin Nano — InsightFace (buffalo_l @ det_size=640)
    # consumes ~1.5 GB on its own and pushes the box into kernel-OOM territory.
    # Set ITIPS_FACE_ENGINE_DISABLED=false in .env to re-enable.
    import os as _os
    if _os.getenv("ITIPS_FACE_ENGINE_DISABLED", "true").lower() == "true":
        class _NoopFaceEngine:
            def recognize(self, frame, detections=None, camera_id=None):
                return []
            def apply_personnel_sync(self, *args, **kwargs):
                return {"status": "disabled", "reason": "face engine disabled"}
            def save_intruder(self, *args, **kwargs):
                return None
        face_engine = _NoopFaceEngine()
    else:
        face_engine = FaceRecognitionEngine(
            model_pack=settings.detection.insightface_pack,
            det_size=settings.detection.insightface_det_size,
            match_threshold=settings.detection.face_match_threshold,
            margin_threshold=settings.detection.face_margin_threshold,
        )
    face_authorizer = FaceAuthorizer(ttl_seconds=settings.detection.face_auth_ttl_seconds)
    plate_client = (
        PlateRecognizerClient(
            url=settings.detection.plate_recognizer_url,
            token=settings.detection.plate_recognizer_token,
        )
        if settings.detection.plate_recognizer_url
        else None
    )

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
        for cam_id in settings.cameras.active().keys()
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
    ptz_controllers = PTZController.build_all()

    def behaviour_factory(camera_id: int):
        return BehaviourAnalyser(camera_id=camera_id)

    deps = WorkerDeps(
        yolo_engine=yolo,
        face_engine=face_engine,
        face_authorizer=face_authorizer,
        plate_recognizer=plate_client,
        behaviour_analyser_factory=behaviour_factory,
        alert_engine=alert_engine,
        ptz_controller=ptz_controllers.get(1),
        frame_bus=frame_bus,
        preset_registry=preset_registry,
    )

    sensor_listener = AXProListener(
        host=settings.sensors.host,
        port=settings.sensors.port,
        username=settings.sensors.username,
        password=settings.sensors.password,
        poll_interval_ms=settings.sensors.poll_interval_ms,
        on_event=alert_engine.handle_sensor_event,
    )

    public_api = PublicApiServer(
        frame_bus=frame_bus,
        alert_engine=alert_engine,
        preset_registry=preset_registry,
        ptz_controllers=ptz_controllers,
    )
    inbound_api = InboundApiServer(
        face_engine=face_engine,
        face_authorizer=face_authorizer,
        ptz_controllers=ptz_controllers,
    )

    services = [intake, evidence_packager, alert_engine, sensor_listener, public_api, inbound_api]
    return deps, services
