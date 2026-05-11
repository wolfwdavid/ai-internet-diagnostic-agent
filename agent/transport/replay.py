"""last_acked_ts cursor for sleep-mid-stream replay (Phase 5 plan 05-02 / Pitfall 5).

Cursor file at ``platformdirs.user_cache_dir/wifi-diag/last_acked.json``.
Atomic-write pattern mirrors ``agent/salt.py`` -- write tmp, rename.

Production caller (``agent/transport/client.py``):
  - On each ``state=streaming`` yield from the Space, call ``save_last_acked_ts``
    with the timestamp of the acked frame.
  - On reconnect after a sleep / blip, ``stream_diagnose`` reads the cursor and
    sends only frames with ``timestamp > last_acked_ts``.
  - On ``state=complete``, the cursor advances to the latest frame in the window
    so a fresh ``agent diagnose --cloud`` starts from a clean slate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import platformdirs


def _cursor_path() -> Path:
    """Return the on-disk path for the replay cursor.

    Uses ``platformdirs.user_cache_dir`` so tests can monkeypatch the location
    via the ``tmp_cache_dir`` fixture.
    """
    p = Path(platformdirs.user_cache_dir("wifi-diag")) / "last_acked.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_last_acked_ts() -> dict:
    """Return the parsed cursor, or defaults on missing / corrupt file.

    Defaults: ``{"last_acked_ts": 0.0, "session_hash": None}``.
    """
    p = _cursor_path()
    if not p.exists():
        return {"last_acked_ts": 0.0, "session_hash": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # Normalize: keep only the two fields we recognize.
        return {
            "last_acked_ts": float(data.get("last_acked_ts", 0.0)),
            "session_hash": data.get("session_hash"),
        }
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return {"last_acked_ts": 0.0, "session_hash": None}


def save_last_acked_ts(ts: float, session_hash: str | None = None) -> None:
    """Atomically persist ``{last_acked_ts, session_hash}`` to the cursor file.

    Mirrors ``agent/salt.py::load_or_create_salt`` write semantics: write to a
    sibling ``.tmp`` file, then ``Path.replace`` to swap atomically. Survives
    crash-during-write.
    """
    p = _cursor_path()
    payload = json.dumps(
        {"last_acked_ts": float(ts), "session_hash": session_hash}
    )
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    tmp.replace(p)


def reset_cursor() -> None:
    """Best-effort delete of the cursor file; no raise on missing."""
    try:
        _cursor_path().unlink()
    except FileNotFoundError:
        pass
