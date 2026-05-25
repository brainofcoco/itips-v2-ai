"""Time primitives. Always use these instead of `datetime.now()` directly.

Two reasons:

1. **Court admissibility.** Every record we sign must carry a monotonic-tick
   anchor alongside the wall-clock timestamp. If the Jetson's wall clock is
   tampered with after the fact, the monotonic tick still proves the ordering
   and elapsed time between events.

2. **Testability.** Tests can patch `now_utc` and `monotonic_ns` without
   monkey-patching the stdlib globally.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="microseconds").replace("+00:00", "Z")


def monotonic_ns() -> int:
    return time.monotonic_ns()
