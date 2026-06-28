"""Rolling 120s telemetry buffer (D-AGENT-01, D-AGENT-04).

SQLite WAL at platformdirs.user_cache_dir()/wifi-diag/buffer/buffer.db.
`frames` table: append-only TelemetryFrame JSON keyed by timestamp.
`flagged_drops` table: drop markers written by daemon's drop-detection heuristic.

Per RESEARCH §Pattern 4: WAL allows daemon to write while `agent diagnose` reads.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import platformdirs
from wifi_diag_schema import TelemetryFrame

_BUFFER_SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    ts REAL NOT NULL,
    frame_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frames_ts ON frames(ts);

CREATE TABLE IF NOT EXISTS flagged_drops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    reason TEXT NOT NULL,
    diagnosed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_flagged_drops_ts ON flagged_drops(ts);
"""


def _buffer_path() -> Path:
    p = Path(platformdirs.user_cache_dir("wifi-diag")) / "buffer" / "buffer.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(_buffer_path(), timeout=30, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_BUFFER_SCHEMA)
    return con


def append_frame(frame: TelemetryFrame) -> None:
    con = _open()
    try:
        con.execute(
            "INSERT INTO frames (ts, frame_json) VALUES (?, ?)",
            (frame.timestamp, frame.model_dump_json()),
        )
    finally:
        con.close()


def snapshot_recent(seconds: int = 120) -> list[TelemetryFrame]:
    cutoff = time.time() - seconds
    con = _open()
    try:
        cur = con.execute("SELECT frame_json FROM frames WHERE ts >= ? ORDER BY ts ASC", (cutoff,))
        return [TelemetryFrame.model_validate_json(row[0]) for row in cur.fetchall()]
    finally:
        con.close()


def flag_drop(ts: float, reason: str) -> int:
    con = _open()
    try:
        cur = con.execute(
            "INSERT INTO flagged_drops (ts, reason, diagnosed) VALUES (?, ?, 0)",
            (ts, reason),
        )
        assert cur.lastrowid is not None  # INSERT always sets lastrowid
        return cur.lastrowid
    finally:
        con.close()


def list_flagged_drops(only_undiagnosed: bool = True) -> list[dict]:
    con = _open()
    try:
        sql = "SELECT id, ts, reason, diagnosed FROM flagged_drops"
        if only_undiagnosed:
            sql += " WHERE diagnosed = 0"
        sql += " ORDER BY ts DESC"
        return [
            {"id": r[0], "ts": r[1], "reason": r[2], "diagnosed": bool(r[3])}
            for r in con.execute(sql).fetchall()
        ]
    finally:
        con.close()


def mark_diagnosed(drop_id: int) -> None:
    con = _open()
    try:
        con.execute("UPDATE flagged_drops SET diagnosed = 1 WHERE id = ?", (drop_id,))
    finally:
        con.close()


def prune_older_than(seconds: int = 120) -> int:
    """Daemon calls periodically to keep the rolling buffer bounded."""
    cutoff = time.time() - seconds
    con = _open()
    try:
        cur = con.execute("DELETE FROM frames WHERE ts < ?", (cutoff,))
        return cur.rowcount
    finally:
        con.close()
