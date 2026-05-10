"""History persistence (AGENT-07 + D-HISTORY-01..04) — freezegun + SQLite WAL."""
from __future__ import annotations

import json

from freezegun import freeze_time
from wifi_diag_schema import Verdict


def _stub_verdict() -> Verdict:
    return Verdict(
        top_class="auth_8021x_eap_fail",
        confidence=0.85,
        top_k=[
            ("auth_8021x_eap_fail", 0.85),
            ("ap_roam_rekey_fail", 0.10),
            ("radius_timeout", 0.05),
        ],
        headline="Your school's 802.1X session failed during AP roaming",
        suggested_fix="Re-enter your school credentials, or contact IT.",
        evidence=[],
    )


def test_history_db_path_at_user_data_dir(monkeypatch, tmp_path):
    """D-HISTORY-04: history.db lives under user_data_dir, NOT user_cache_dir.

    The default tmp_state_dir fixture redirects user_data_dir and user_cache_dir
    to sibling subdirs of the same tmp_path; that's enough to assert separation,
    but we make the assertion explicit by patching to DISTINCT roots so the test
    can't pass vacuously if both dirs ever resolve to the same path.
    """
    data_root = tmp_path / "DATA"
    cache_root = tmp_path / "CACHE"
    state_root = tmp_path / "STATE"
    config_root = tmp_path / "CONFIG"
    for d in (data_root, cache_root, state_root, config_root):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: str(data_root))
    monkeypatch.setattr("platformdirs.user_cache_dir", lambda *a, **k: str(cache_root))
    monkeypatch.setattr("platformdirs.user_state_dir", lambda *a, **k: str(state_root))
    monkeypatch.setattr("platformdirs.user_config_dir", lambda *a, **k: str(config_root))

    from agent.history import _history_path

    p = _history_path()
    assert "wifi-diag" in str(p)
    assert p.name == "history.db"
    assert str(data_root) in str(p), (
        f"history.db must be under user_data_dir ({data_root}); got {p}"
    )
    assert str(cache_root) not in str(p), (
        f"D-HISTORY-04 violation: history.db under user_cache_dir ({cache_root}); got {p}"
    )


def test_write_and_read_diagnosis(tmp_state_dir, synthetic_frame):
    from agent.history import list_diagnoses, show_diagnosis, write_diagnosis

    verdict = _stub_verdict()
    window = [synthetic_frame for _ in range(5)]
    diag_id = write_diagnosis(
        verdict=verdict, telemetry_window=window, consent_level="local"
    )
    assert isinstance(diag_id, int) and diag_id > 0

    rows = list_diagnoses()
    assert len(rows) == 1
    assert rows[0]["id"] == diag_id

    full = show_diagnosis(diag_id)
    assert full is not None
    assert full["verdict_json"]
    assert full["telemetry_json"]  # D-HISTORY-01 — telemetry persisted


def test_row_stores_telemetry_window(tmp_state_dir, synthetic_frame):
    """D-HISTORY-01: Phase 5 Reality Anchor seed pipeline depends on telemetry persistence."""
    from agent.history import show_diagnosis, write_diagnosis

    window = [synthetic_frame for _ in range(3)]
    diag_id = write_diagnosis(
        verdict=_stub_verdict(), telemetry_window=window, consent_level="local"
    )
    full = show_diagnosis(diag_id)
    assert full is not None
    loaded = json.loads(full["telemetry_json"])
    assert isinstance(loaded, list)
    assert len(loaded) == 3


def test_90_day_retention_prune_with_freezegun(tmp_state_dir, synthetic_frame):
    """D-HISTORY-02: auto-prune rows older than 90 days."""
    from agent.history import list_diagnoses, prune_older_than, write_diagnosis

    with freeze_time("2026-01-01"):
        write_diagnosis(_stub_verdict(), [synthetic_frame], "local")
    with freeze_time("2026-04-15"):  # ~104 days later
        n_pruned = prune_older_than(days=90)
        rows = list_diagnoses()
    assert n_pruned == 1
    assert len(rows) == 0


def test_clear_keep_last_n(tmp_state_dir, synthetic_frame):
    from agent.history import clear, list_diagnoses, write_diagnosis

    ids = []
    for _ in range(5):
        ids.append(write_diagnosis(_stub_verdict(), [synthetic_frame], "local"))
    clear(keep_last=2)
    rows = list_diagnoses()
    assert len(rows) == 2
    # keep_last keeps the most recent IDs
    kept_ids = sorted(r["id"] for r in rows)
    assert kept_ids == sorted(ids[-2:])


def test_clear_all(tmp_state_dir, synthetic_frame):
    from agent.history import clear, list_diagnoses, write_diagnosis

    for _ in range(3):
        write_diagnosis(_stub_verdict(), [synthetic_frame], "local")
    clear()
    assert len(list_diagnoses()) == 0
