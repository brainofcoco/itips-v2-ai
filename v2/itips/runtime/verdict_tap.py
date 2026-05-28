"""History of ThreatEvaluator verdicts — the "why didn't it escalate?" feed.

Every time the multi-frame decision window closes, the evaluator emits a
verdict (AUTHORIZED / UNCERTAIN / INTRUDER, the last optionally
suppressed because the panel was disarmed). This tap keeps a record so
the dashboard's Investigations page can show every evaluation and,
crucially, *why* an intrusion did or didn't escalate to an alarm —
matched worker, no usable face, system disarmed, or confirmed intruder.

Backed by SQLite so the history survives container restarts. If no
`db_path` is given (tests, degraded boot) it falls back to an in-memory
ring so the API still works, just without persistence.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL NOT NULL,
    verdict           TEXT NOT NULL,
    camera_id         INTEGER,
    armed             INTEGER NOT NULL DEFAULT 0,
    escalated         INTEGER NOT NULL DEFAULT 0,
    outcome           TEXT NOT NULL,
    reason            TEXT NOT NULL,
    person_name       TEXT NOT NULL DEFAULT '',
    person_uid        TEXT NOT NULL DEFAULT '',
    similarity        REAL,
    samples           INTEGER,
    saw_face          INTEGER,
    best_no_match_sim REAL,
    triggers          TEXT NOT NULL DEFAULT '[]',
    trigger_details   TEXT NOT NULL DEFAULT '[]',
    window_s          REAL,
    capture_id        TEXT,
    capture_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_verdicts_ts ON verdicts(ts DESC);
"""

# Columns added after the initial schema shipped — applied to pre-existing
# DBs via ALTER TABLE (sqlite has no "ADD COLUMN IF NOT EXISTS").
_MIGRATIONS = [
    ("capture_id", "ALTER TABLE verdicts ADD COLUMN capture_id TEXT"),
    ("capture_count", "ALTER TABLE verdicts ADD COLUMN capture_count INTEGER NOT NULL DEFAULT 0"),
]


class VerdictTap:
    # Keep the table bounded without an explicit retention job.
    MAX_ROWS = 10_000

    def __init__(self, db_path: Optional[Path] = None, *, capacity: int = 300) -> None:
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        # In-memory fallback when there's no DB (tests / degraded boot).
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counter = 0
        if db_path is not None:
            try:
                p = Path(db_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(str(p), check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.executescript(_SCHEMA)
                self._apply_migrations()
                self._conn.commit()
                logger.info("VerdictTap persisting to %s", p)
            except Exception:
                logger.exception("VerdictTap: sqlite init failed — using in-memory ring")
                self._conn = None

    def _apply_migrations(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(verdicts)")}
        for col, ddl in _MIGRATIONS:
            if col not in cols:
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    logger.exception("VerdictTap: migration for %s failed", col)

    def publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Wrap a raw verdict payload with derived `escalated`, `outcome`
        category, and a human `reason`, then persist it."""
        verdict = str(payload.get("verdict") or "")
        armed = bool(payload.get("armed"))
        escalated = bool(payload.get("alarm_fired")) or (verdict == "intruder" and armed)
        outcome, reason = _classify(verdict, armed, escalated, payload)
        entry = {
            "ts": time.time(),
            "verdict": verdict,
            "camera_id": payload.get("camera_id"),
            "armed": armed,
            "escalated": escalated,
            "outcome": outcome,
            "reason": reason,
            "person_name": payload.get("person_name") or "",
            "person_uid": payload.get("person_uid") or "",
            "similarity": _maybe_float(payload.get("similarity")),
            "samples": payload.get("samples"),
            "saw_face": payload.get("saw_face"),
            "best_no_match_sim": _maybe_float(payload.get("best_no_match_sim")),
            "triggers": payload.get("triggers") or [],
            "trigger_details": payload.get("trigger_details") or [],
            "window_s": _maybe_float(payload.get("window_s")),
            "capture_id": payload.get("capture_id"),
            "capture_count": int(payload.get("capture_count") or 0),
        }
        with self._lock:
            if self._conn is not None:
                cur = self._conn.execute(
                    "INSERT INTO verdicts "
                    "(ts, verdict, camera_id, armed, escalated, outcome, reason, "
                    " person_name, person_uid, similarity, samples, saw_face, "
                    " best_no_match_sim, triggers, trigger_details, window_s, "
                    " capture_id, capture_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry["ts"], entry["verdict"], entry["camera_id"],
                        int(entry["armed"]), int(entry["escalated"]),
                        entry["outcome"], entry["reason"], entry["person_name"],
                        entry["person_uid"], entry["similarity"], entry["samples"],
                        _maybe_int(entry["saw_face"]), entry["best_no_match_sim"],
                        json.dumps(entry["triggers"]), json.dumps(entry["trigger_details"]),
                        entry["window_s"], entry["capture_id"], entry["capture_count"],
                    ),
                )
                entry["seq"] = cur.lastrowid
                self._prune_locked()
                self._conn.commit()
            else:
                self._counter += 1
                entry["seq"] = self._counter
                self._buf.append(entry)
        return entry

    def recent(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        with self._lock:
            if self._conn is not None:
                sql = "SELECT * FROM verdicts ORDER BY ts DESC, seq DESC"
                params: tuple = ()
                if limit is not None:
                    sql += " LIMIT ?"
                    params = (int(limit),)
                rows = self._conn.execute(sql, params).fetchall()
                return [_row_to_dict(r) for r in rows]
            items = list(self._buf)
        if limit is not None:
            items = items[-limit:]
        return list(reversed(items))

    def clear(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.execute("DELETE FROM verdicts")
                self._conn.commit()
            else:
                self._buf.clear()

    def _prune_locked(self) -> None:
        # Caller holds the lock. Drop everything past the newest MAX_ROWS.
        self._conn.execute(
            "DELETE FROM verdicts WHERE seq NOT IN "
            "(SELECT seq FROM verdicts ORDER BY ts DESC, seq DESC LIMIT ?)",
            (self.MAX_ROWS,),
        )


def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": r["seq"],
        "ts": r["ts"],
        "verdict": r["verdict"],
        "camera_id": r["camera_id"],
        "armed": bool(r["armed"]),
        "escalated": bool(r["escalated"]),
        "outcome": r["outcome"],
        "reason": r["reason"],
        "person_name": r["person_name"],
        "person_uid": r["person_uid"],
        "similarity": r["similarity"],
        "samples": r["samples"],
        "saw_face": None if r["saw_face"] is None else bool(r["saw_face"]),
        "best_no_match_sim": r["best_no_match_sim"],
        "triggers": _load_json(r["triggers"]),
        "trigger_details": _load_json(r["trigger_details"]),
        "window_s": r["window_s"],
        "capture_id": _row_get(r, "capture_id"),
        "capture_count": _row_get(r, "capture_count") or 0,
    }


def _row_get(r: sqlite3.Row, key: str) -> Any:
    try:
        return r[key]
    except (IndexError, KeyError):
        return None


def _load_json(s: Any) -> Any:
    try:
        return json.loads(s) if s else []
    except (TypeError, ValueError):
        return []


def _maybe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _maybe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    return int(bool(v)) if isinstance(v, bool) else int(v)


def _classify(
    verdict: str, armed: bool, escalated: bool, payload: dict[str, Any],
) -> tuple[str, str]:
    """Return (outcome_category, human_reason)."""
    if verdict == "authorized":
        name = payload.get("person_name") or "a known worker"
        sim = payload.get("similarity")
        sim_pct = f" ({round(float(sim) * 100)}%)" if isinstance(sim, (int, float)) else ""
        return "authorized", f"Not escalated — matched {name}{sim_pct}"
    if verdict == "uncertain":
        n = payload.get("samples")
        return "uncertain", (
            f"Not escalated — no usable face in {n} sample(s); "
            "couldn't confirm intruder vs worker"
        )
    if verdict == "intruder":
        if escalated:
            return "escalated", "Escalated — confirmed intruder, alarm fired"
        return "suppressed_disarmed", (
            "Not escalated — intruder detected but the system was disarmed"
        )
    return verdict or "unknown", f"Verdict: {verdict or 'unknown'}"
