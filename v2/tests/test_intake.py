import json
import sqlite3
import time
from pathlib import Path

import pytest

from itips.sync.intake import IntakeWriter
from itips.sync.schema import IntakePacket, Priority
from itips.utils.clock import monotonic_ns, now_iso


def _packet(priority: Priority, endpoint: str = "A1", body: dict | None = None) -> IntakePacket:
    return IntakePacket(
        priority=priority,
        endpoint_hint=endpoint,
        payload=body or {"ping": True},
        timestamp_utc=now_iso(),
        monotonic_ns=monotonic_ns(),
    )


def _wait_for_rows(db_path: Path, expected: int, timeout: float = 2.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if db_path.exists():
            try:
                count = sqlite3.connect(db_path).execute(
                    "SELECT COUNT(*) FROM intake"
                ).fetchone()[0]
                if count >= expected:
                    return count
            except sqlite3.OperationalError:
                # Writer thread created the file but not yet the table; retry.
                pass
        time.sleep(0.05)
    pytest.fail(f"Expected {expected} rows in {db_path}, did not appear in {timeout}s")
    return -1  # unreachable


def test_intake_writes_and_persists(tmp_intake_db):
    writer = IntakeWriter(tmp_intake_db, flush_ms=10)
    writer.start()
    try:
        writer.enqueue(_packet(Priority.HEARTBEAT))
        _wait_for_rows(tmp_intake_db, 1)
    finally:
        writer.stop()
        writer.join(timeout=2)

    rows = sqlite3.connect(tmp_intake_db).execute(
        "SELECT priority, endpoint_hint, status FROM intake"
    ).fetchall()
    assert rows == [(int(Priority.HEARTBEAT), "A1", "pending")]


def test_intake_preserves_priority_order_on_drain(tmp_intake_db):
    writer = IntakeWriter(tmp_intake_db, flush_ms=10)
    writer.start()
    try:
        # Enqueue out of priority order; the drain index sorts on read.
        for p in (Priority.HEARTBEAT, Priority.RAPID_DISPATCH, Priority.MEDIA_CAPTURE):
            writer.enqueue(_packet(p))
        _wait_for_rows(tmp_intake_db, 3)
    finally:
        writer.stop()
        writer.join(timeout=2)

    drained = sqlite3.connect(tmp_intake_db).execute(
        "SELECT priority FROM intake WHERE status='pending' ORDER BY priority ASC, id ASC"
    ).fetchall()
    assert drained == [(1,), (3,), (5,)]


def test_intake_payload_is_json(tmp_intake_db):
    writer = IntakeWriter(tmp_intake_db, flush_ms=10)
    writer.start()
    try:
        writer.enqueue(_packet(Priority.INCIDENT_EVENT, body={"alert": "intrusion", "n": 1}))
        _wait_for_rows(tmp_intake_db, 1)
    finally:
        writer.stop()
        writer.join(timeout=2)

    raw = sqlite3.connect(tmp_intake_db).execute("SELECT payload FROM intake").fetchone()[0]
    assert json.loads(raw) == {"alert": "intrusion", "n": 1}


def test_full_buffer_drops_instead_of_blocking(tmp_intake_db):
    writer = IntakeWriter(tmp_intake_db, flush_ms=1000)  # slow flush
    # Don't start the thread — so nothing drains and we can saturate the queue.
    writer._inbox.maxsize = 2  # type: ignore[attr-defined]
    writer.enqueue(_packet(Priority.HEARTBEAT))
    writer.enqueue(_packet(Priority.HEARTBEAT))
    writer.enqueue(_packet(Priority.HEARTBEAT))  # would block — must drop
    assert writer._inbox.qsize() == 2  # type: ignore[attr-defined]
