"""Daemon process lifecycle (D-AGENT-01).

Per CONTEXT.md Discretion: simple subprocess.Popen with per-OS detach flags;
NO Windows service / launchd / systemd-user registration at v1.

Pitfall 8 mitigation: stale PID guard via is_pid_alive (no zombie reads).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import platformdirs


def _state_root() -> Path:
    p = Path(platformdirs.user_state_dir("wifi-diag"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pid_file() -> Path:
    return _state_root() / "daemon.pid"


def _start_marker() -> Path:
    return _state_root() / "daemon.started_at"


def _log_file() -> Path:
    log_root = Path(platformdirs.user_log_dir("wifi-diag"))
    log_root.mkdir(parents=True, exist_ok=True)
    return log_root / "daemon.log"


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(ok) and exit_code.value == STILL_ACTIVE
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def _read_pid() -> int | None:
    pf = _pid_file()
    if not pf.exists():
        return None
    try:
        return int(pf.read_text().strip())
    except (ValueError, OSError):
        return None


def _clear_pid() -> None:
    pf = _pid_file()
    if pf.exists():
        try:
            pf.unlink()
        except OSError:
            pass
    sm = _start_marker()
    if sm.exists():
        try:
            sm.unlink()
        except OSError:
            pass


def start_daemon() -> str:
    existing = _read_pid()
    if existing is not None and is_pid_alive(existing):
        return f"already running pid={existing}"
    # Stale PID — clean up
    if existing is not None:
        _clear_pid()

    log = _log_file().open("a")
    if sys.platform == "win32":
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent.daemon"],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent.daemon"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            close_fds=True,
        )

    # Atomic PID file write
    tmp = _pid_file().with_suffix(".pid.tmp")
    tmp.write_text(str(proc.pid))
    tmp.replace(_pid_file())
    _start_marker().write_text(str(time.time()))
    return f"daemon started pid={proc.pid}"


def stop_daemon() -> str:
    pid = _read_pid()
    if pid is None or not is_pid_alive(pid):
        _clear_pid()
        return "no daemon running"
    try:
        if sys.platform == "win32":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        return f"error stopping daemon pid={pid}: {e}"
    _clear_pid()
    return f"daemon stopped pid={pid}"


def pause_daemon() -> str:
    """Signal the daemon to stop sampling but stay alive (D-AGENT-03 'pause').

    Implementation: write a 'paused' marker file the daemon polls each sample tick.
    """
    marker = _state_root() / "daemon.paused"
    marker.write_text(str(time.time()))
    return "daemon paused (sampling stopped; process kept alive)"


def resume_daemon() -> str:
    marker = _state_root() / "daemon.paused"
    if marker.exists():
        marker.unlink()
    return "daemon resumed"


def daemon_status() -> dict:
    pid = _read_pid()
    running = pid is not None and is_pid_alive(pid)
    if not running:
        if pid is not None:
            _clear_pid()
        return {"running": False, "pid": None, "uptime_s": None, "paused": False}
    sm = _start_marker()
    uptime = (time.time() - float(sm.read_text())) if sm.exists() else None
    paused = (_state_root() / "daemon.paused").exists()
    return {"running": True, "pid": pid, "uptime_s": uptime, "paused": paused}
