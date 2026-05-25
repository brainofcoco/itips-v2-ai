"""SQLite-backed priority queue — the only outbound channel from this pipeline.

Design constraints:
  - Writes are append-only. AI code never deletes or updates rows; the
    Sync Agent owns the read-and-mark-synced lifecycle.
  - The queue is bounded by `max_age_days` for status='pending'. Heartbeats
    (priority 5) get aged off first when space pressure hits.
  - WAL mode + a single writer thread keep us safe under concurrent
    enqueue from multiple camera workers without blocking.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from config.settings import settings
from itips.sync.schema import IntakePacket, Priority
from itips.utils.clock import monotonic_ns, now_iso

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intake (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    priority      INTEGER NOT NULL,
    endpoint_hint TEXT    NOT NULL,
    payload       TEXT    NOT NULL,
    timestamp_utc TEXT    NOT NULL,
    monotonic_ns  INTEGER NOT NULL,
    incident_id   TEXT,
    operator_id   TEXT,
    site_id       TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending',
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_intake_drain ON intake(status, priority ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_intake_incident ON intake(incident_id);
"""


class IntakeWriter(threading.Thread):
    """Background writer that serialises packets to SQLite.

    Camera workers and the alert engine call `enqueue(packet)` which is
    O(1) and non-blocking. The writer thread drains the in-memory queue
    in batches and commits with WAL.
    """

    def __init__(self, db_path: Path, batch_size: int = 32, flush_ms: int = 100) -> None:
        super().__init__(name="intake-writer", daemon=True)
        self._db_path = Path(db_path)
        self._batch_size = batch_size
        self._flush_ms = flush_ms
        self._inbox: "queue.Queue[IntakePacket]" = queue.Queue(maxsize=10_000)
        self._stop = threading.Event()
        self._db: sqlite3.Connection | None = None

    # ─── public surface ────────────────────────────────────────────

    def enqueue(self, packet: IntakePacket) -> None:
        """Non-blocking. Drops with a log line if the buffer is full
        — almost always a signal that the Sync Agent has died, not a
        signal to stall the detection loop.
        """
        try:
            self._inbox.put_nowait(packet)
        except queue.Full:
            logger.error("Intake buffer FULL — dropping packet (endpoint=%s, priority=%s)",
                         packet.endpoint_hint, packet.priority)

    def emit(
        self,
        *,
        priority: Priority,
        endpoint_hint: str,
        payload: dict,
        incident_id: str | None = None,
    ) -> None:
        """Convenience wrapper used by the alert engine and packager."""
        packet = IntakePacket(
            priority=priority,
            endpoint_hint=endpoint_hint,
            payload=payload,
            timestamp_utc=now_iso(),
            monotonic_ns=monotonic_ns(),
            incident_id=incident_id,
            operator_id=settings.tenant.operator_id or None,
            site_id=settings.tenant.site_id or None,
        )
        self.enqueue(packet)

    def stop(self) -> None:
        self._stop.set()

    # ─── thread body ───────────────────────────────────────────────

    def run(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._db_path), isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.execute("PRAGMA synchronous=NORMAL;")
        with self._db:
            self._db.executescript(_SCHEMA)
        logger.info("Intake writer ready at %s", self._db_path)

        batch: list[IntakePacket] = []
        while not self._stop.is_set() or not self._inbox.empty() or batch:
            try:
                packet = self._inbox.get(timeout=self._flush_ms / 1000)
                batch.append(packet)
            except queue.Empty:
                pass

            if batch and (len(batch) >= self._batch_size or self._stop.is_set() or self._inbox.empty()):
                self._flush(batch)
                batch = []

        try:
            self._db.close()
        except Exception:
            pass
        logger.info("Intake writer stopped.")

    def _flush(self, batch: Iterable[IntakePacket]) -> None:
        assert self._db is not None
        try:
            with self._db:
                self._db.executemany(
                    "INSERT INTO intake "
                    "(priority, endpoint_hint, payload, timestamp_utc, monotonic_ns, "
                    " incident_id, operator_id, site_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            int(p.priority),
                            p.endpoint_hint,
                            json.dumps(p.payload, separators=(",", ":")),
                            p.timestamp_utc,
                            p.monotonic_ns,
                            p.incident_id,
                            p.operator_id,
                            p.site_id,
                        )
                        for p in batch
                    ],
                )
        except sqlite3.Error:
            logger.exception("Intake flush failed; %d packets lost", len(list(batch)))
