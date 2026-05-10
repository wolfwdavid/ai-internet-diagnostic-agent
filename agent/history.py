"""Local diagnosis history (AGENT-07 + D-HISTORY-01..04).

SQLite WAL at ``platformdirs.user_data_dir/wifi-diag/history.db``.

Decisions:
- D-HISTORY-01: stores Verdict JSON + telemetry-window JSON (Phase 5 Reality
  Anchor seed pipeline depends on the telemetry).
- D-HISTORY-02: 90-day default retention; auto-prune on each write_diagnosis().
- D-HISTORY-03: plaintext SQLite (schema-allowlist redaction already strips
  PII upstream; encryption-at-rest deferred to v1.x).
- D-HISTORY-04: ``user_data_dir`` (durable user data) — NOT ``user_cache_dir``.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import platformdirs
from wifi_diag_schema import TelemetryFrame, Verdict

try:
    from wifi_diag_schema import SCHEMA_VERSION
except ImportError:  # older schema export shape
    SCHEMA_VERSION = "1.1.0"

DEFAULT_RETENTION_DAYS = 90

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    verdict_json TEXT NOT NULL,
    telemetry_json TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    consent_level TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnoses_ts ON diagnoses(ts);
"""


def _history_path() -> Path:
    p = Path(platformdirs.user_data_dir("wifi-diag")) / "history.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(_history_path(), timeout=30, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_SCHEMA_DDL)
    return con


def write_diagnosis(
    verdict: Verdict,
    telemetry_window: list[TelemetryFrame],
    consent_level: str,
    ts: float | None = None,
) -> int:
    """Persist a diagnosis (D-HISTORY-01) and auto-prune 90-day-old rows (D-HISTORY-02)."""
    ts = ts if ts is not None else time.time()
    verdict_json = verdict.model_dump_json()
    # D-HISTORY-01: telemetry window persisted alongside the verdict.
    telemetry_json = json.dumps([f.model_dump(mode="json") for f in telemetry_window])
    con = _open()
    try:
        cur = con.execute(
            "INSERT INTO diagnoses (ts, verdict_json, telemetry_json, schema_version, consent_level) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, verdict_json, telemetry_json, SCHEMA_VERSION, consent_level),
        )
        new_id = int(cur.lastrowid)
        # Auto-prune (D-HISTORY-02).
        cutoff = time.time() - (DEFAULT_RETENTION_DAYS * 86400)
        con.execute("DELETE FROM diagnoses WHERE ts < ?", (cutoff,))
    finally:
        con.close()
    return new_id


def list_diagnoses(limit: int = 100) -> list[dict]:
    con = _open()
    try:
        cur = con.execute(
            "SELECT id, ts, schema_version, consent_level "
            "FROM diagnoses ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "id": r[0],
                "ts": r[1],
                "schema_version": r[2],
                "consent_level": r[3],
            }
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def show_diagnosis(diag_id: int) -> dict | None:
    con = _open()
    try:
        cur = con.execute(
            "SELECT id, ts, verdict_json, telemetry_json, schema_version, consent_level "
            "FROM diagnoses WHERE id = ?",
            (diag_id,),
        )
        r = cur.fetchone()
        if r is None:
            return None
        return {
            "id": r[0],
            "ts": r[1],
            "verdict_json": r[2],
            "telemetry_json": r[3],
            "schema_version": r[4],
            "consent_level": r[5],
        }
    finally:
        con.close()


def prune_older_than(days: int = DEFAULT_RETENTION_DAYS) -> int:
    cutoff = time.time() - (days * 86400)
    con = _open()
    try:
        cur = con.execute("DELETE FROM diagnoses WHERE ts < ?", (cutoff,))
        return cur.rowcount
    finally:
        con.close()


def clear(keep_last: int | None = None) -> int:
    """Delete all rows, optionally keeping the most-recent ``keep_last``."""
    con = _open()
    try:
        if keep_last is None or keep_last <= 0:
            cur = con.execute("DELETE FROM diagnoses")
            return cur.rowcount
        cur = con.execute(
            "DELETE FROM diagnoses WHERE id NOT IN ("
            "  SELECT id FROM diagnoses ORDER BY ts DESC LIMIT ?"
            ")",
            (keep_last,),
        )
        return cur.rowcount
    finally:
        con.close()


def set_retention_days(days: int) -> None:
    """D-HISTORY-02: configurable retention; takes effect at next write_diagnosis()."""
    con = _open()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS history_config "
            "(key TEXT PRIMARY KEY, value TEXT)"
        )
        con.execute(
            "INSERT INTO history_config (key, value) VALUES ('retention_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(days),),
        )
    finally:
        con.close()
    global DEFAULT_RETENTION_DAYS
    DEFAULT_RETENTION_DAYS = days
