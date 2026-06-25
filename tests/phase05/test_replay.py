"""TDD tests for agent.transport.replay (Phase 5 plan 05-02 task 1).

Cursor file at ``platformdirs.user_cache_dir/wifi-diag/last_acked.json``
with atomic-write semantics and corruption recovery.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.transport.replay import (
    _cursor_path,
    load_last_acked_ts,
    reset_cursor,
    save_last_acked_ts,
)


def test_cursor_default_when_missing(tmp_cache_dir):
    assert load_last_acked_ts() == {"last_acked_ts": 0.0, "session_hash": None}


def test_save_and_reload_roundtrip(tmp_cache_dir):
    save_last_acked_ts(1234.5, "abc")
    assert load_last_acked_ts() == {"last_acked_ts": 1234.5, "session_hash": "abc"}


def test_cursor_atomic_write(tmp_cache_dir, monkeypatch):
    """Verify the atomic-write tmp + rename pattern is used."""
    seen: list[Path] = []
    real_replace = Path.replace

    def _record_replace(self, target):
        seen.append(self)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _record_replace)
    save_last_acked_ts(7.0, "h")
    assert seen, "Path.replace must be called to rename the tmp file"
    assert any(str(p).endswith(".json.tmp") for p in seen), (
        f"replace must be called with a .tmp source; got {seen}"
    )


def test_cursor_corruption_recovers(tmp_cache_dir):
    """Garbled cursor JSON must yield defaults, no raise."""
    p = _cursor_path()
    p.write_text("{not valid json", encoding="utf-8")
    assert load_last_acked_ts() == {"last_acked_ts": 0.0, "session_hash": None}


def test_reset_cursor_deletes(tmp_cache_dir):
    save_last_acked_ts(10.0, "x")
    assert _cursor_path().exists()
    reset_cursor()
    assert _cursor_path().exists() is False


def test_reset_missing_no_raise(tmp_cache_dir):
    """reset_cursor with no prior save must not raise."""
    reset_cursor()


def test_advance_on_ack_simulates_streaming(tmp_cache_dir):
    """Simulate 5 streaming acks; cursor advances monotonically."""
    last = 0.0
    for ts in [1.0, 2.0, 3.0, 4.0, 5.0]:
        save_last_acked_ts(ts, "sess")
        loaded = load_last_acked_ts()
        assert loaded["last_acked_ts"] >= last
        assert loaded["last_acked_ts"] == ts
        last = loaded["last_acked_ts"]
    # Final cursor sticks.
    raw = json.loads(_cursor_path().read_text())
    assert raw == {"last_acked_ts": 5.0, "session_hash": "sess"}
