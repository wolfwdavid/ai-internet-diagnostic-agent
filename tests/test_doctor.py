"""`agent doctor` rendering — per-OS health check (D-PRIV-02)."""

from __future__ import annotations

import platform

import pytest

from agent.doctor import render_doctor_table


def test_baseline_row_present(tmp_state_dir, mocker, capsys):
    # Mock psutil + icmplib check to succeed
    mocker.patch(
        "agent.doctor._check_baseline",
        return_value={
            "name": "ICMP + psutil baseline",
            "status": "ok",
            "unlocks": "60% of classifier signal across all OSes",
        },
    )
    # Mock the per-OS check so this test runs on any OS
    mocker.patch("agent.doctor._check_current_os", return_value=[])
    render_doctor_table()
    out = capsys.readouterr().out
    assert "baseline" in out.lower()


def test_per_os_row_present(capsys, mocker):
    # Force the per-OS branch via real check (not mocked) — we only assert the row label.
    os_name = platform.system()
    if os_name == "Windows":
        expected = "WLAN-AutoConfig"
    elif os_name == "Darwin":
        expected = "CoreWLAN"
    elif os_name == "Linux":
        expected = "NetworkManager"
    else:
        pytest.skip(f"Unsupported OS for this test: {os_name}")
    render_doctor_table()
    out = capsys.readouterr().out
    assert expected in out, f"Expected {expected!r} in doctor output for {os_name}: {out!r}"
