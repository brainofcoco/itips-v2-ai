"""Filter the process's stderr stream to drop known-benign noise.

Libraries like libjpeg-turbo, ffmpeg, and PaddlePaddle write directly
to file descriptor 2 (the raw OS stderr) rather than going through
Python's `sys.stderr`. That means a `sys.stderr = …` reassignment
or a Python-level handler cannot intercept them — the only way to
suppress these messages is to redirect fd 2 itself.

This module installs a one-time pump:

  1. Save the original fd 2 (so we can still write through it).
  2. Replace fd 2 with the write end of a pipe.
  3. Spawn a daemon thread that reads the pipe line by line.
  4. Each line is checked against a list of substring filters; matching
     lines are dropped, everything else is forwarded to the original
     fd 2 so real errors still surface.

This is heavy-handed by design — it operates on every byte the
process writes to stderr — but the alternative is operators staring
at "Corrupt JPEG data" spam at 30+ lines/minute on a quiet site,
which masks genuine problems and erodes trust in the logs.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)


_DEFAULT_DROP_SUBSTRINGS: tuple[bytes, ...] = (
    # libjpeg-turbo: Dahua snapshots/event JPEGs carry non-standard
    # segments that decode fine but trigger this warning on every frame.
    b"Corrupt JPEG data",
    # PaddlePaddle: cosmetic warning about ccache not being installed
    # inside the container — same message every cold start of EasyOCR.
    b"No ccache found",
)


_installed = False
_install_lock = threading.Lock()


def install_stderr_filter(extra_drops: tuple[bytes, ...] = ()) -> None:
    """Idempotently install the stderr pump.

    `extra_drops` lets callers add their own per-site noise filters.
    """
    global _installed
    with _install_lock:
        if _installed:
            return
        drops = _DEFAULT_DROP_SUBSTRINGS + tuple(extra_drops)
        try:
            original_fd = os.dup(2)
        except OSError:
            # Sandbox without fd table tricks — abort silently.
            return
        read_fd, write_fd = os.pipe()
        # Splice stderr → write_fd; everything written to fd 2 from
        # here on (Python, C extensions, ffmpeg, libjpeg, …) lands on
        # the pipe's read end.
        os.dup2(write_fd, 2)
        os.close(write_fd)

        def _pump() -> None:
            # Line-buffered read so a long-running C library that
            # doesn't flush after every line doesn't block our forward.
            buf = b""
            while True:
                try:
                    chunk = os.read(read_fd, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while True:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break
                    line = buf[: nl + 1]
                    buf = buf[nl + 1 :]
                    if any(needle in line for needle in drops):
                        continue
                    try:
                        os.write(original_fd, line)
                    except OSError:
                        return

        t = threading.Thread(
            target=_pump,
            name="stderr-filter",
            daemon=True,
        )
        t.start()
        _installed = True
        # Log via the original fd so this confirmation isn't filtered.
        try:
            os.write(
                original_fd,
                b"[stderr-filter] active; dropping noise: "
                + b", ".join(drops) + b"\n",
            )
        except OSError:
            pass
