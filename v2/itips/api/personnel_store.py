"""Local map: cloud `person_id` → per-camera Dahua UID.

The cloud personnel database uses its own ID for each enrolled worker
(see PRD §4.5 / B1 contract). Each Dahua camera assigns its own UID at
`addPerson` time. We persist that mapping so a later `deactivate` can
find every camera UID to delete.

SQLite for durability across reboots. The table is tiny (≤ a few
hundred rows per site) so we keep it in the same NVMe path that owns
the intake queue.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS personnel (
    person_id   TEXT PRIMARY KEY,
    full_name   TEXT NOT NULL,
    per_camera  TEXT NOT NULL,           -- JSON {camera_id: uid}
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass
class PersonnelRecord:
    person_id: str
    full_name: str
    per_camera: dict[int, str] = field(default_factory=dict)


class PersonnelStore:
    """Thread-safe SQLite-backed personnel ↔ camera-UID map."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None
        self._open()

    def _open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(self._db_path), isolation_level=None, check_same_thread=False
        )
        self._db.execute("PRAGMA journal_mode=WAL;")
        with self._db:
            self._db.executescript(_SCHEMA)

    # ─── CRUD ─────────────────────────────────────────────────────────

    def upsert(self, *, person_id: str, full_name: str, per_camera: dict[int, str]) -> None:
        payload = json.dumps({str(k): v for k, v in per_camera.items()})
        with self._lock:
            assert self._db is not None
            self._db.execute(
                "INSERT INTO personnel (person_id, full_name, per_camera) VALUES (?, ?, ?) "
                "ON CONFLICT(person_id) DO UPDATE SET "
                "full_name=excluded.full_name, "
                "per_camera=excluded.per_camera, "
                "updated_at=CURRENT_TIMESTAMP",
                (person_id, full_name, payload),
            )

    def get(self, person_id: str) -> Optional[PersonnelRecord]:
        with self._lock:
            assert self._db is not None
            row = self._db.execute(
                "SELECT person_id, full_name, per_camera FROM personnel WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        if row is None:
            return None
        return _to_record(row)

    def delete(self, person_id: str) -> None:
        with self._lock:
            assert self._db is not None
            self._db.execute("DELETE FROM personnel WHERE person_id = ?", (person_id,))

    def list_all(self) -> list[PersonnelRecord]:
        with self._lock:
            assert self._db is not None
            rows = self._db.execute(
                "SELECT person_id, full_name, per_camera FROM personnel ORDER BY full_name"
            ).fetchall()
        return [_to_record(r) for r in rows]

    # ─── service shim ─────────────────────────────────────────────────

    def start(self) -> None:
        return

    def stop(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:
                    pass
                self._db = None


def _to_record(row: tuple[str, str, str]) -> PersonnelRecord:
    person_id, full_name, blob = row
    try:
        raw = json.loads(blob)
        per_camera = {int(k): str(v) for k, v in raw.items()}
    except (ValueError, TypeError):
        per_camera = {}
    return PersonnelRecord(person_id=person_id, full_name=full_name, per_camera=per_camera)
