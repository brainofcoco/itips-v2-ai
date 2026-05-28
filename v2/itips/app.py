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
    _quiet_libjpeg_noise()
    # libjpeg-turbo / PaddlePaddle write some warnings *directly* to
    # fd 2, bypassing both Python's logging and OpenCV's logger.
    # Install a fd-level filter that drops the known-cosmetic lines.
    from itips.utils.stderr_filter import install_stderr_filter
    install_stderr_filter()
    logger = logging.getLogger("itips.app")
    logger.info("ITIPS V2 (Dahua-native) starting — mode=%s site=%s device=%s",
                settings.mode, settings.tenant.site_id or "(unset)",
                settings.tenant.device_id or "(unset)")

    orchestrator = Orchestrator(deps_factory=_build_deps)
    return orchestrator.run()


def _quiet_libjpeg_noise() -> None:
    """Drop OpenCV's log threshold below WARN.

    libjpeg-turbo (used by cv2 for JPEG decode) writes warnings like
    ``Corrupt JPEG data: N extraneous bytes before marker 0xfe`` for
    images that decode fine but contain non-standard segments. Dahua
    snapshots and event JPEGs trip this constantly. The messages are
    cosmetic — the decoded image is correct — but they spam the logs
    at ~30 lines/minute on a quiet site, which masks real errors and
    confuses operators reading the log.

    We silence WARN-level OpenCV messages globally; ERROR and FATAL
    still surface, so a genuine decode failure is still visible.
    """
    import os
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    try:
        import cv2
        # cv2 ≥ 4.7 exposes setLogLevel directly; older builds need the
        # utils.logging module. Try both — failure is non-fatal.
        try:
            cv2.setLogLevel(3)            # 3 == LOG_LEVEL_ERROR
        except AttributeError:
            from cv2.utils import logging as _cvlog
            _cvlog.setLogLevel(_cvlog.LOG_LEVEL_ERROR)
    except Exception:
        # No cv2, or an older version without setLogLevel — the env var
        # above is the belt-and-braces path.
        pass


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
    from itips.camera.rtsp_grabber import build_grabbers
    from itips.evidence.packager import EvidencePackager
    from itips.evidence.recorder import IncidentRecorder
    from itips.runtime.event_worker import WorkerDeps
    from itips.sync.intake import IntakeWriter
    from itips.webhooks import SubscriberStore, WebhookDispatcher

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
        idle_timeout_seconds=settings.incident.idle_timeout_seconds,
    )

    frame_bus = FrameBus()
    event_tap = EventTap()
    # Real-time "something just happened" feed (line cross, intrusion,
    # sensor trip) for the Live page — published before any model
    # validation or incident packaging.
    from itips.runtime.activity_tap import ActivityTap
    activity_tap = ActivityTap()

    # Continuous RTSP grabbers feed:
    #   1. IncidentRecorder — so pre/post MP4s contain actual footage.
    #   2. FrameBus         — so /api/snapshot/{cam} always has a recent
    #                         frame to return without hammering the
    #                         camera's HTTP /cgi-bin/snapshot.cgi per
    #                         dashboard tile per tick.
    rtsp_grabbers = build_grabbers(
        settings.cameras.active(),
        target_fps=settings.evidence.grabber_fps,
    )
    for cam_id, grabber in rtsp_grabbers.items():
        recorder = recorders.get(cam_id)
        if recorder is not None:
            grabber.add_consumer(recorder.feed)
        grabber.add_consumer(_frame_bus_publisher(frame_bus, cam_id))

    # Shared "what preset is each camera currently at" tracker. Built
    # before the ML layer so the BehaviorEngine can gate zone evaluation
    # on the camera being at the zone's bound preset. Sensor dispatcher
    # and the dashboard PTZ routes also report into it.
    from itips.camera.preset_state import PresetStateTracker
    preset_state = PresetStateTracker()

    # Per-camera operator settings — currently just `base_preset_name`,
    # which drives the auto-restore on RTSP reconnect below.
    from itips.camera.camera_settings import CameraSettingsStore
    camera_settings = CameraSettingsStore(
        path=personnel_store_path.parent / "camera_settings.json",
    )

    # Auto-restore on disrupt/reconnect: when the RTSP stream drops,
    # clear preset_state so zones stop firing against the wrong view.
    # When it comes back, pan the camera to its configured base preset
    # (if any) so we re-establish a known orientation. Without this, a
    # power blip leaves the camera parked at whatever the firmware
    # decides and the operator has to click a preset by hand before
    # zone evaluation can resume.
    _wire_camera_recovery(
        rtsp_grabbers=rtsp_grabbers,
        dahua_manager=dahua_manager,
        preset_state=preset_state,
        camera_settings=camera_settings,
    )
    # Recovery watcher — catches the cases the RTSP listeners miss:
    # short stutters where cv2 keeps the capture alive, container
    # restarts (preset_state is in-memory), or a reconnect goto that
    # fired before the camera's HTTP API was ready. Restores to the
    # base preset whenever a camera's tracker is empty.
    from itips.camera.recovery_watcher import CameraRecoveryWatcher
    recovery_watcher = CameraRecoveryWatcher(
        dahua_manager=dahua_manager,
        preset_state=preset_state,
        camera_settings=camera_settings,
    )

    # ML fallback — all optional; engines only init if ml extras installed.
    ml_state = _build_ml_layer(
        embedding_db_path=personnel_store_path.parent / "face_embeddings.sqlite",
        zones_path=personnel_store_path.parent / "zones.json",
        overrides_path=personnel_store_path.parent / "ml_overrides.json",
        preset_state=preset_state,
    )
    openai_validator = _build_openai_validator()

    # Multi-frame decision engine — collapses every primary trigger
    # (camera line/face/region + sensor activation) into one 15s window
    # that samples multiple snapshots before deciding INTRUDER vs
    # AUTHORIZED vs UNCERTAIN. Disabled when no face engine is loaded so
    # the legacy direct-to-incident path keeps working in barebones dev.
    verdict_capture_dir = personnel_store_path.parent / "verdict_captures"
    threat_evaluator = _build_threat_evaluator(
        alert_engine=alert_engine,
        dahua_manager=dahua_manager,
        face_engine=ml_state.face_engine,
        capture_dir=verdict_capture_dir,
    )

    # Sensor pipeline. Dispatcher works without face_engine — just
    # pans + snapshots without the auto-validate step.
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
        threat_evaluator=threat_evaluator,
        preset_state=preset_state,
        activity_tap=activity_tap,
    )

    # Continuous zone evaluation — runs the behavior engine against the
    # live RTSP frames (preset-gated) so line-cross / intrusion / loiter
    # are detected by ITIPS in real time and surfaced on Live, rather
    # than only when the camera's own IVS fires.
    from itips.runtime.behavior_watcher import BehaviorWatcher
    behavior_watcher = BehaviorWatcher(
        frame_bus=frame_bus,
        behavior_engine=ml_state.behavior_engine,
        dahua_manager=dahua_manager,
        activity_tap=activity_tap,
        threat_evaluator=threat_evaluator,
        target_fps=2.0,
    )

    # AX PRO hub listener — None unless ITIPS_AXPRO_* env are set.
    # Polls zone_status for slow-signal edges (door magnetOpenStatus).
    axpro_listener = _build_axpro_listener(sensor_dispatcher)
    # Bind the AX PRO arming state into the evaluator's disarm gate.
    # Done after listener construction because the listener needs the
    # dispatcher and the dispatcher needs the evaluator; without a
    # listener the evaluator stays in default-armed mode (fail-safe).
    if threat_evaluator is not None and axpro_listener is not None:
        threat_evaluator.set_is_armed_fn(lambda: axpro_listener.is_armed)

    # AX PRO real-time event stream — multipart long-poll on
    # /ISAPI/Event/notification/alertStream. Carries cidEvent /
    # zoneEvent payloads that zone_status polling misses (PIR pulses,
    # tamper, low-battery, etc.). Shares the listener's authenticated
    # session.
    axpro_alertstream = _build_axpro_alertstream(axpro_listener, sensor_dispatcher)
    # Investigations feed — record every ThreatEvaluator verdict so the
    # dashboard can show which intrusions escalated and, more usefully,
    # which didn't and why (worker matched / no face / disarmed).
    from itips.runtime.verdict_tap import VerdictTap
    verdict_tap = VerdictTap(db_path=personnel_store_path.parent / "verdicts.sqlite")
    if threat_evaluator is not None:
        threat_evaluator.add_verdict_listener(verdict_tap.publish)

    # Hub-control helper for the dashboard's hub-admin routes.
    axpro_admin = _build_axpro_admin(axpro_listener)
    # Sound the hub siren only on a ThreatEvaluator INTRUDER verdict —
    # after ITIPS has looked at the scene — rather than letting the
    # panel auto-sound on every sensor trip. Gated by
    # ITIPS_AXPRO_AUTO_SIREN (off in dev so synthetic events stay quiet).
    _wire_auto_siren(threat_evaluator, axpro_admin)

    # Outbound webhooks — fan AlertEngine + validator + sensor events
    # out to registered subscriber URLs. Always built (so the dashboard
    # CRUD works); the worker threads simply sit idle when no
    # subscribers exist.
    webhook_store = SubscriberStore(db_path=settings.webhooks.db_path)
    webhook_dispatcher = WebhookDispatcher(
        store=webhook_store,
        timeout_s=settings.webhooks.timeout_s,
        workers=settings.webhooks.workers,
        max_queue=settings.webhooks.max_queue,
    ) if settings.webhooks.enabled else None
    if webhook_dispatcher is not None:
        webhook_dispatcher.bind_alert_engine(alert_engine)
        webhook_dispatcher.bind_openai_validator(openai_validator)
        webhook_dispatcher.bind_sensor_dispatcher(sensor_dispatcher)
        # Final decision-window verdict — what alarm panels and the hub
        # should subscribe to for the clean "this is what we decided".
        webhook_dispatcher.bind_threat_evaluator(
            threat_evaluator, tenant=settings.tenant,
        )
        # AX PRO raw-stream firehose — sensor.event already covers
        # dispatched activations; this lets a subscriber consume
        # cidEvents (low-battery, tamper, arm/disarm) that don't enter
        # the dispatcher.
        if axpro_alertstream is not None:
            from itips.webhooks.events import WebhookEvent
            def _raw_axpro(data):
                webhook_dispatcher.dispatch(WebhookEvent(
                    kind="sensor.event",
                    data={"axpro_raw": data},
                    site_id=settings.tenant.site_id or None,
                ))
            axpro_alertstream.add_raw_listener(_raw_axpro)

    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=frame_bus,
        recorders=recorders,
        event_tap=event_tap,
        capability_router=ml_state.router,
        face_engine=ml_state.face_engine,
        plate_engine=ml_state.plate_engine,
        behavior_engine=ml_state.behavior_engine,
        openai_validator=openai_validator,
        threat_evaluator=threat_evaluator,
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
        axpro_alertstream=axpro_alertstream,
        axpro_admin=axpro_admin,
        openai_validator=openai_validator,
        webhook_store=webhook_store,
        webhook_dispatcher=webhook_dispatcher,
        preset_state=preset_state,
        camera_settings=camera_settings,
        activity_tap=activity_tap,
        verdict_tap=verdict_tap,
        verdict_capture_dir=verdict_capture_dir,
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
    # RTSP grabbers must outlive the orchestrator's worker loop — added
    # to services so they get the same start()/stop() lifecycle.
    services.extend(rtsp_grabbers.values())
    services.append(recovery_watcher)
    services.append(behavior_watcher)
    if threat_evaluator is not None:
        services.append(threat_evaluator)
    if ml_state.embedding_store is not None:
        services.append(ml_state.embedding_store)
    services.append(sensor_dispatcher)
    if axpro_listener is not None:
        services.append(axpro_listener)
    if axpro_alertstream is not None:
        services.append(axpro_alertstream)
    services.append(webhook_store)
    if webhook_dispatcher is not None:
        services.append(webhook_dispatcher)
    return deps, services


def _frame_bus_publisher(frame_bus, camera_id):
    """Adapter so RtspFrameGrabber consumers can push into FrameBus.

    The grabber's contract is `Callable[[np.ndarray], None]`; FrameBus
    wants a `FrameSnapshot`. Wrap the frame in the snapshot each tick.
    Returns a closure to avoid one allocation of the publisher per call.
    """
    from itips.runtime.frame_bus import FrameSnapshot
    from itips.utils.clock import monotonic_ns

    def publish(frame) -> None:
        frame_bus.publish(FrameSnapshot(
            camera_id=camera_id,
            raw=frame,
            annotated=frame,
            monotonic_ns=monotonic_ns(),
            preset_id="grabber",
        ))

    return publish


def _build_threat_evaluator(*, alert_engine, dahua_manager, face_engine, capture_dir=None):
    """Return a started ThreatEvaluator, or None when prerequisites are
    missing. Settings-disabled or no face engine ⇒ None ⇒ event_worker
    and sensor_dispatcher fall back to their legacy direct paths."""
    logger = logging.getLogger("itips.app.threat")
    if not settings.threat_evaluator.enabled:
        logger.info("ThreatEvaluator disabled by settings — direct alert path active")
        return None
    if face_engine is None:
        logger.info("ThreatEvaluator disabled — no face engine loaded "
                    "(install ml extras to enable)")
        return None
    try:
        from itips.runtime.threat_evaluator import ThreatEvaluator
    except Exception:
        logger.exception("ThreatEvaluator import failed")
        return None
    return ThreatEvaluator(
        alert_engine=alert_engine,
        dahua_manager=dahua_manager,
        face_engine=face_engine,
        window_seconds=settings.threat_evaluator.window_seconds,
        sample_interval_s=settings.threat_evaluator.sample_interval_s,
        capture_dir=capture_dir,
    )


def _build_openai_validator():
    """Returns None unless ITIPS_OPENAI_ENABLED=true and key + prompts exist."""
    import os
    logger = logging.getLogger("itips.app.openai")
    if (os.environ.get("ITIPS_OPENAI_ENABLED") or "").lower() not in {"1", "true", "yes"}:
        return None
    api_key = (os.environ.get("ITIPS_OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OpenAI validator disabled — ITIPS_OPENAI_ENABLED=true "
                       "but ITIPS_OPENAI_API_KEY is empty")
        return None
    try:
        from itips.ml import OpenAIValidator
    except Exception:
        logger.exception("OpenAIValidator import failed")
        return None
    prompts_path = Path(os.environ.get("ITIPS_OPENAI_PROMPTS_PATH",
                                       "config/prompts.yaml"))
    model = os.environ.get("ITIPS_OPENAI_MODEL", "gpt-4o-mini")
    max_tokens = int(os.environ.get("ITIPS_OPENAI_MAX_TOKENS_PER_HOUR", "100000") or "100000")
    timeout_s = float(os.environ.get("ITIPS_OPENAI_TIMEOUT_S", "30") or "30")
    return OpenAIValidator(
        api_key=api_key, prompts_path=prompts_path,
        default_model=model, enabled=True,
        max_tokens_per_hour=max_tokens,
        timeout_s=timeout_s,
    )


def _build_axpro_alertstream(listener, dispatcher):
    """Returns None unless the zone-status listener is wired. The
    alertStream shares the listener's authenticated hikaxpro session so
    we don't open a second login on the hub."""
    if listener is None:
        return None
    logger = logging.getLogger("itips.app.axpro")
    try:
        from itips.sensors.axpro_alertstream import AxProAlertStream
    except Exception:
        logger.exception("AxProAlertStream import failed")
        return None
    return AxProAlertStream(
        host=listener.host,
        client_supplier=listener.get_client,
        dispatcher=dispatcher,
    )


def _build_axpro_admin(listener):
    """Returns None unless the zone-status listener is wired."""
    if listener is None:
        return None
    from itips.sensors.axpro_admin import AxProAdmin
    return AxProAdmin(host=listener.host, client_supplier=listener.get_client)


def _wire_camera_recovery(
    *, rtsp_grabbers, dahua_manager, preset_state, camera_settings,
) -> None:
    """Attach disrupt + reconnect handlers to every camera's RTSP grabber.

    Disrupt → clear the camera's entry in `preset_state` so zones go
    dormant for the duration of the outage (we no longer know where the
    camera is pointing).

    Reconnect → look up the operator-configured base preset; if one is
    set, pan there and record the new orientation. We sleep briefly
    before the HTTP call because the camera's web stack is typically a
    couple of seconds behind RTSP when the device is rebooting (RTSP
    streams from a leaner subsystem). Without the wait, the first PTZ
    call after a hard reboot tends to land on a closed socket and the
    operator gets a useless retry on the next reconnect.
    """
    import time as _time

    recovery_log = logging.getLogger("itips.app.recovery")

    def _on_disrupt(cam_id: int) -> None:
        preset_state.clear(cam_id)
        recovery_log.info(
            "cam%d: stream dropped — cleared preset_state (zones dormant)",
            cam_id,
        )

    def _on_reconnect(cam_id: int) -> None:
        settings_for = camera_settings.get(cam_id)
        base = settings_for.base_preset_name
        if not base:
            recovery_log.info(
                "cam%d: reconnected — no base preset configured, leaving "
                "preset_state empty (zones stay dormant until operator "
                "selects a preset)",
                cam_id,
            )
            return
        client = dahua_manager.get(cam_id)
        if client is None:
            recovery_log.warning(
                "cam%d: reconnected but DahuaManager has no client — "
                "skipping base-preset restore",
                cam_id,
            )
            return
        # Give the camera's HTTP API a moment to catch up with RTSP. On
        # a clean reconnect this is wasted; on a full reboot it's the
        # difference between success and a 502 we never retry.
        _time.sleep(3.0)
        try:
            ok = client.ptz.goto_preset_by_name(base)
        except Exception:
            recovery_log.exception(
                "cam%d: base-preset restore to %r crashed", cam_id, base,
            )
            return
        if not ok:
            recovery_log.warning(
                "cam%d: base-preset %r not found on camera (preset "
                "renamed or deleted?) — zones stay dormant",
                cam_id, base,
            )
            return
        preset_state.record_goto(cam_id, base)
        recovery_log.info(
            "cam%d: reconnected → panned to base preset %r", cam_id, base,
        )

    for cam_id, grabber in rtsp_grabbers.items():
        grabber.add_disrupt_listener(_on_disrupt)
        grabber.add_reconnect_listener(_on_reconnect)


def _wire_auto_siren(threat_evaluator, axpro_admin) -> None:
    """Sound the hub siren only when the ThreatEvaluator confirms an
    INTRUDER — i.e. *after* ITIPS has panned, sampled the scene, and
    failed to match an authorised worker. This is the "go check first,
    then sound it" behaviour: the hub itself stays silent (see each
    zone's `silentEnabled` flag) and ITIPS is the sole siren trigger.

    Opt-in via ITIPS_AXPRO_AUTO_SIREN=true; default off so a dev box
    running synthetic events doesn't blast a live 100 dB siren. With it
    off, the verdict still flows through the rest of the pipeline
    (incident, webhooks) — only the physical siren is withheld.
    """
    import os
    if axpro_admin is None or threat_evaluator is None:
        return
    logger = logging.getLogger("itips.app.axpro")
    enabled = (os.environ.get("ITIPS_AXPRO_AUTO_SIREN") or "").lower() in {"1", "true", "yes"}
    sub_id = int(os.environ.get("ITIPS_AXPRO_AUTO_SIREN_SUBSYS") or "1")

    def _on_verdict(payload: dict) -> None:
        if payload.get("verdict") != "intruder" or not payload.get("armed"):
            return
        cam = payload.get("camera_id")
        if not enabled:
            logger.warning(
                "INTRUDER on cam%s — code-siren is DISABLED "
                "(set ITIPS_AXPRO_AUTO_SIREN=true to sound subsys %d)",
                cam, sub_id,
            )
            return
        try:
            axpro_admin.start_siren(sub_id)
            logger.warning(
                "code-siren: SOUNDED subsys %d on confirmed INTRUDER (cam%s)",
                sub_id, cam,
            )
        except Exception:
            logger.exception("code-siren: start_siren failed")

    threat_evaluator.add_verdict_listener(_on_verdict)
    logger.info(
        "Code-siren wired to INTRUDER verdict (subsys %d) — enabled=%s",
        sub_id, enabled,
    )


def _build_axpro_listener(dispatcher):
    """Returns None when host/creds aren't set or hikaxpro missing."""
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
                    overrides_path, preset_state=None) -> "_MlLayerState":
    """Each engine fails independently — failure must never break boot."""
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
            preset_state=preset_state,
        )
        state.behavior_engine.warmup_async()
    except Exception:
        logger.exception("behavior fallback disabled (will degrade to motion-only)")

    return state
