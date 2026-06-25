"""Wave 0 RED-state tests for daemon process lifecycle (D-AGENT-01)."""

from __future__ import annotations

import time

from agent.process import daemon_status, is_pid_alive, start_daemon, stop_daemon


def test_start_daemon_writes_pid_file(tmp_state_dir):
    msg = start_daemon()
    assert "daemon started pid=" in msg
    status = daemon_status()
    assert status["running"] is True
    assert status["pid"] is not None
    assert is_pid_alive(status["pid"])
    # Cleanup
    stop_daemon()


def test_stop_daemon_clears_pid(tmp_state_dir):
    start_daemon()
    stop_daemon()
    # Allow a moment for OS to reap
    time.sleep(0.5)
    status = daemon_status()
    assert status["running"] is False


def test_double_start_reports_already_running(tmp_state_dir):
    first = start_daemon()
    second = start_daemon()
    assert "daemon started" in first
    assert "already running" in second
    stop_daemon()
