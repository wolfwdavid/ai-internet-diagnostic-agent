"""`agent privacy` command renderer (PRIV-03)."""
from __future__ import annotations

from agent.privacy import render_privacy


def test_privacy_command_prints_schema_field_list(tmp_state_dir, capsys):
    render_privacy()
    out = capsys.readouterr().out
    for required_field in ("timestamp", "os", "network_mode", "rssi_dbm", "bssid"):
        assert required_field in out, (
            f"PRIVACY output missing schema field {required_field!r}"
        )


def test_privacy_command_prints_effective_config(tmp_state_dir, capsys):
    render_privacy()
    out = capsys.readouterr().out
    assert "default consent" in out.lower()
    assert "local" in out.lower()
    assert "90" in out  # retention days
    assert "v1.0.0" in out  # model revision
