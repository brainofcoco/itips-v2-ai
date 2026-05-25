"""Structured logging setup. One configurator, called once at startup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from config.settings import settings

_FMT = "%(asctime)s [%(threadName)s] %(levelname)-7s %(name)s — %(message)s"


def configure(log_file: Path | None = None) -> None:
    """Set up logging for the whole process.

    Idempotent — safe to call from any module. Subsequent calls re-set
    handlers so test suites can rotate logging cleanly.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level.upper())

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(_FMT))
    root.addHandler(stream)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FMT))
        root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("zeep").setLevel(logging.WARNING)
