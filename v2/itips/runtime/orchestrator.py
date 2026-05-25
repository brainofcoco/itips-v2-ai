"""Process supervisor — boots and tears down every long-lived component.

The orchestrator does no per-frame work. Its job is to construct
components, start their threads, and shut them down cleanly on signal.
If you find yourself adding inference code here, you're in the wrong file.
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Iterable

from config.settings import settings
from itips.runtime.camera_worker import CameraWorker, WorkerDeps
from itips.runtime.frame_bus import FrameBus

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, deps_factory: callable) -> None:
        """deps_factory returns a fully built WorkerDeps and any long-lived
        services (sensors, packager, intake, sync writer, api servers).

        Built lazily so the orchestrator stays small and testable.
        """
        self._deps_factory = deps_factory
        self._workers: list[CameraWorker] = []
        self._services: list = []
        self._stop_event = threading.Event()

    def run(self) -> int:
        deps, services = self._deps_factory()
        self._services = list(services)

        for service in self._services:
            service.start()
            logger.info("Started service: %s", service.__class__.__name__)

        self._workers = self._build_workers(deps)
        for worker in self._workers:
            worker.start()

        self._install_signal_handlers()
        logger.info("Orchestrator running with %d camera worker(s)", len(self._workers))

        try:
            self._stop_event.wait()
        finally:
            self._shutdown()
        return 0

    def _build_workers(self, deps: WorkerDeps) -> list:
        """Build per-camera workers. Worker class is chosen by ITIPS_CAMERA_MODE.

        * "streaming" (default) — CameraWorker: continuous RTSP decode + 24/7
          inference. Designed footprint, runs on Orin NX 16GB / AGX Orin.
        * "event_driven" — EventDrivenWorker: idle until camera fires a
          motion / line-cross / object-detect event, then fetches a snapshot
          and runs single-frame inference. Fits on Orin Nano 8GB.
        """
        import os
        mode = os.getenv("ITIPS_CAMERA_MODE", "streaming").strip().lower()
        if mode == "event_driven":
            from itips.runtime.event_worker import EventDrivenWorker
            worker_cls = EventDrivenWorker
            logger.info("Camera mode: event_driven (Dahua eventManager + snapshot)")
        else:
            worker_cls = CameraWorker
            logger.info("Camera mode: streaming (continuous RTSP decode)")
        workers = []
        for camera_id, url in settings.cameras.active().items():
            workers.append(worker_cls(camera_id, url, deps))
        if not workers:
            logger.warning("No active cameras configured. Pipeline will run idle.")
        return workers

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            logger.info("Signal %s received; initiating shutdown.", signum)
            self._stop_event.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def _shutdown(self) -> None:
        logger.info("Stopping camera workers...")
        for worker in self._workers:
            worker.stop()
        for worker in self._workers:
            worker.join(timeout=5.0)

        logger.info("Stopping services...")
        for service in reversed(self._services):
            stopper = getattr(service, "stop", None)
            if callable(stopper):
                try:
                    stopper()
                except Exception:
                    logger.exception("Failed to stop service %s", service.__class__.__name__)

        logger.info("Shutdown complete.")
