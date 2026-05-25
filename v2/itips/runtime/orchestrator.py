"""Process supervisor — boots and tears down every long-lived component.

The orchestrator does no per-event work. Its job is to construct
components, start their threads, and shut them down cleanly on signal.
"""

from __future__ import annotations

import logging
import signal
import threading

from config.settings import settings
from itips.runtime.event_worker import DahuaEventDispatcher, WorkerDeps

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, deps_factory: callable) -> None:
        """deps_factory returns (WorkerDeps, services).

        Built lazily so the orchestrator stays small and testable.
        """
        self._deps_factory = deps_factory
        self._workers: list[DahuaEventDispatcher] = []
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
        logger.info("Orchestrator running with %d Dahua dispatcher(s)", len(self._workers))

        try:
            self._stop_event.wait()
        finally:
            self._shutdown()
        return 0

    def _build_workers(self, deps: WorkerDeps) -> list[DahuaEventDispatcher]:
        workers: list[DahuaEventDispatcher] = []
        for camera_id, url in settings.cameras.active().items():
            workers.append(DahuaEventDispatcher(camera_id, url, deps))
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
        logger.info("Stopping Dahua dispatchers...")
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
