"""SQLite-backed subscriber registry.

Schema is intentionally small — one row per subscriber, one row per
recent delivery attempt. The delivery log is capped (oldest pruned past
`max_log_rows`) so the file stays bounded without explicit retention.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    secret        TEXT NOT NULL,
    event_filter  TEXT NOT NULL,           -- JSON array; ["*"] = all
    description   TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    last_status        INTEGER,            -- last HTTP status
    last_error         TEXT,
    last_delivered_at  REAL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS deliveries (
    id             TEXT PRIMARY KEY,
    subscriber_id  TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    event_kind     TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    status_code    INTEGER,
    error          TEXT,
    duration_ms    INTEGER,
    delivered_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deliveries_sub
    ON deliveries(subscriber_id, delivered_at DESC);
"""


@dataclass
class Subscriber:
    id: str
    url: str
    secret: str
    event_filter: list[str]
    description: str = ""
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_status: int | None = None
    last_error: str | None = None
    last_delivered_at: float | None = None
    consecutive_failures: int = 0

    def matches(self, event_kind: str) -> bool:
        if not self.enabled:
            return False
        if "*" in self.event_filter:
            return True
        return event_kind in self.event_filter

    def to_public(self, *, include_secret: bool = False) -> dict[str, Any]:
        """Dict for API responses. Secret omitted unless explicitly asked."""
        out = {
            "id": self.id,
            "url": self.url,
            "event_filter": list(self.event_filter),
            "description": self.description,
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_delivered_at": self.last_delivered_at,
            "consecutive_failures": self.consecutive_failures,
        }
        if include_secret:
            out["secret"] = self.secret
        return out


class SubscriberStore:
    """Thread-safe sqlite-backed subscriber + delivery log store."""

    MAX_DELIVERY_LOG_ROWS = 5_000

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # sqlite3 connections are not safe across threads by default;
        # we serialise access through `_lock`. The store sees low write
        # volume (mostly delivery logging) so a single connection is
        # plenty.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ─── subscriber CRUD ──────────────────────────────────────────

    def create(
        self,
        *,
        url: str,
        secret: str,
        event_filter: Iterable[str],
        description: str = "",
        enabled: bool = True,
    ) -> Subscriber:
        sub = Subscriber(
            id=str(uuid.uuid4()),
            url=url.strip(),
            secret=secret,
            event_filter=list(event_filter),
            description=description.strip(),
            enabled=enabled,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO subscribers "
                "(id, url, secret, event_filter, description, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sub.id, sub.url, sub.secret, json.dumps(sub.event_filter),
                 sub.description, int(sub.enabled), sub.created_at, sub.updated_at),
            )
            self._conn.commit()
        return sub

    def get(self, subscriber_id: str) -> Subscriber | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM subscribers WHERE id = ?", (subscriber_id,),
            ).fetchone()
        return self._row_to_sub(row) if row else None

    def list(self) -> list[Subscriber]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM subscribers ORDER BY created_at"
            ).fetchall()
        return [self._row_to_sub(r) for r in rows]

    def update(self, subscriber_id: str, **fields: Any) -> Subscriber | None:
        """Patch a subscriber. Only the listed fields are accepted."""
        allowed = {"url", "secret", "event_filter", "description", "enabled"}
        patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not patch:
            return self.get(subscriber_id)
        if "event_filter" in patch:
            patch["event_filter"] = json.dumps(list(patch["event_filter"]))
        if "enabled" in patch:
            patch["enabled"] = int(bool(patch["enabled"]))
        patch["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in patch)
        with self._lock:
            self._conn.execute(
                f"UPDATE subscribers SET {cols} WHERE id = ?",
                (*patch.values(), subscriber_id),
            )
            self._conn.commit()
        return self.get(subscriber_id)

    def delete(self, subscriber_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM subscribers WHERE id = ?", (subscriber_id,),
            )
            self._conn.execute(
                "DELETE FROM deliveries WHERE subscriber_id = ?",
                (subscriber_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ─── delivery bookkeeping ─────────────────────────────────────

    def record_delivery(
        self,
        *,
        subscriber_id: str,
        event_id: str,
        event_kind: str,
        attempt: int,
        status_code: int | None,
        error: str | None,
        duration_ms: int,
        success: bool,
    ) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO deliveries (id, subscriber_id, event_id, event_kind, "
                "attempt, status_code, error, duration_ms, delivered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), subscriber_id, event_id, event_kind,
                 attempt, status_code, error, duration_ms, now),
            )
            if success:
                self._conn.execute(
                    "UPDATE subscribers SET last_status=?, last_error=NULL, "
                    "last_delivered_at=?, consecutive_failures=0, updated_at=? "
                    "WHERE id=?",
                    (status_code, now, now, subscriber_id),
                )
            else:
                self._conn.execute(
                    "UPDATE subscribers SET last_status=?, last_error=?, "
                    "consecutive_failures=consecutive_failures+1, updated_at=? "
                    "WHERE id=?",
                    (status_code, (error or "")[:500], now, subscriber_id),
                )
            self._prune_locked()
            self._conn.commit()

    def list_deliveries(
        self,
        *,
        subscriber_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            if subscriber_id:
                rows = self._conn.execute(
                    "SELECT * FROM deliveries WHERE subscriber_id = ? "
                    "ORDER BY delivered_at DESC LIMIT ?",
                    (subscriber_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM deliveries ORDER BY delivered_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ─── service shim ─────────────────────────────────────────────

    def start(self) -> None:
        return

    def stop(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ─── internals ────────────────────────────────────────────────

    def _row_to_sub(self, row: sqlite3.Row) -> Subscriber:
        try:
            event_filter = json.loads(row["event_filter"]) or ["*"]
            if not isinstance(event_filter, list):
                event_filter = ["*"]
        except (TypeError, ValueError, json.JSONDecodeError):
            event_filter = ["*"]
        return Subscriber(
            id=row["id"],
            url=row["url"],
            secret=row["secret"],
            event_filter=event_filter,
            description=row["description"] or "",
            enabled=bool(row["enabled"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_status=row["last_status"],
            last_error=row["last_error"],
            last_delivered_at=row["last_delivered_at"],
            consecutive_failures=int(row["consecutive_failures"] or 0),
        )

    def _prune_locked(self) -> None:
        """Drop oldest delivery rows past the cap. Called under _lock."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM deliveries").fetchone()
        if row and row["n"] > self.MAX_DELIVERY_LOG_ROWS:
            overflow = row["n"] - self.MAX_DELIVERY_LOG_ROWS
            self._conn.execute(
                "DELETE FROM deliveries WHERE id IN ("
                "  SELECT id FROM deliveries ORDER BY delivered_at ASC LIMIT ?"
                ")",
                (overflow,),
            )
