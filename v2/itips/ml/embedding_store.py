"""SQLite-backed face embedding DB for the Jetson recognition fallback.

Embeddings are raw little-endian float32 bytes — avoids JSON-base64
bloat and keeps load to a single `np.frombuffer` call.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS face_embeddings (
    person_id   TEXT PRIMARY KEY,
    full_name   TEXT NOT NULL,
    embedding   BLOB NOT NULL,        -- L2-normalised float32 vector
    dim         INTEGER NOT NULL,     -- vector length (512 for ArcFace)
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass
class EmbeddingRecord:
    person_id: str
    full_name: str
    embedding: "np.ndarray"     # shape (dim,), L2-normalised
    dim: int


class EmbeddingStore:
    """Thread-safe SQLite-backed face embedding store."""

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

    def upsert(self, *, person_id: str, full_name: str, embedding: "np.ndarray") -> None:
        import numpy as np  # noqa: F401
        vec = embedding.astype("float32", copy=False)
        if vec.ndim != 1:
            raise ValueError(f"embedding must be 1-D, got shape {vec.shape}")
        # L2-normalise on write so cosine similarity is a plain dot product.
        norm = float((vec @ vec) ** 0.5)
        if norm > 0:
            vec = vec / norm
        blob = vec.tobytes()
        dim = int(vec.shape[0])
        with self._lock:
            assert self._db is not None
            self._db.execute(
                "INSERT INTO face_embeddings (person_id, full_name, embedding, dim) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(person_id) DO UPDATE SET "
                "full_name=excluded.full_name, "
                "embedding=excluded.embedding, "
                "dim=excluded.dim, "
                "updated_at=CURRENT_TIMESTAMP",
                (person_id, full_name, blob, dim),
            )

    def delete(self, person_id: str) -> bool:
        with self._lock:
            assert self._db is not None
            cur = self._db.execute(
                "DELETE FROM face_embeddings WHERE person_id = ?", (person_id,),
            )
            return cur.rowcount > 0

    def get(self, person_id: str) -> Optional[EmbeddingRecord]:
        with self._lock:
            assert self._db is not None
            row = self._db.execute(
                "SELECT person_id, full_name, embedding, dim "
                "FROM face_embeddings WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_all(self) -> list[EmbeddingRecord]:
        with self._lock:
            assert self._db is not None
            rows = self._db.execute(
                "SELECT person_id, full_name, embedding, dim "
                "FROM face_embeddings ORDER BY full_name"
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            assert self._db is not None
            row = self._db.execute(
                "SELECT COUNT(*) FROM face_embeddings"
            ).fetchone()
        return int(row[0]) if row else 0

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


def _row_to_record(row: tuple) -> EmbeddingRecord:
    import numpy as np
    person_id, full_name, blob, dim = row
    vec = np.frombuffer(blob, dtype="float32")
    if vec.shape[0] != int(dim):
        # Corrupted row — keep going but log loudly.
        logger.error("embedding row %s has dim %d but blob holds %d floats",
                     person_id, int(dim), vec.shape[0])
    return EmbeddingRecord(
        person_id=str(person_id),
        full_name=str(full_name),
        embedding=vec.copy(),   # detach from sqlite-owned buffer
        dim=int(dim),
    )
