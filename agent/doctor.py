"""Per-OS health check (D-PRIV-02).

Renders a ``rich.table`` with rows for:
  - The cross-OS baseline (psutil + icmplib — works everywhere without admin).
  - Per-OS data sources (Win: WLAN-AutoConfig event log; macOS: CoreWLAN
    signing + ``log show``; Linux: NetworkManager D-Bus).

Each row has a status (✓ / ⚠ / ✗) and a "what unlocking adds" column —
educational tone, does NOT offer to elevate inline (avoids UAC / antivirus
flagging per Pitfall 13).
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any

from rich.console import Console
from rich.table import Table

_STATUS_GLYPHS: dict[str, str] = {
    "ok":   "[green]✓[/green]",   # ✓
    "warn": "[yellow]⚠[/yellow]",  # ⚠
    "err":  "[red]✗[/red]",        # ✗
}


def _check_baseline() -> dict[str, Any]:
    """psutil + icmplib are pure-Python; both work on all 3 OSes without admin."""
    try:
        import icmplib  # noqa: F401
        import psutil  # noqa: F401
        return {
            "name": "ICMP + psutil baseline",
            "status": "ok",
            "unlocks": "60% of classifier signal — works on every OS without admin",
        }
    except ImportError as e:
        return {
            "name": "ICMP + psutil baseline",
            "status": "err",
            "unlocks": f"missing dep: {e}",
        }


def _check_windows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        import win32evtlog  # type: ignore[import-not-found]  # noqa: F401
        rows.append({
            "name": "WLAN-AutoConfig event log",
            "status": "ok",
            "unlocks": "+25% confidence on auth_8021x_eap_fail (D-PRIV-03)",
        })
    except ImportError:
        rows.append({
            "name": "WLAN-AutoConfig event log",
            "status": "err",
            "unlocks": "install with `pip install ai-internet-diagnostic-agent[windows]`",
        })
    return rows


def _check_macos() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # CoreWLAN signing — Sonoma 14.4+ returns None for SSID/BSSID on
    # ad-hoc-signed Python even with Location Services granted.
    try:
        from CoreWLAN import CWInterface  # type: ignore[import-not-found]
        iface = CWInterface.sharedInstance()
        has_network = bool(getattr(iface, "serviceActive", lambda: False)())
        bssid = iface.bssid() if iface else None
        if has_network and not bssid:
            rows.append({
                "name": "CoreWLAN signing",
                "status": "warn",
                "unlocks": (
                    "ad-hoc-signed Python returns None for BSSID on Sonoma 14.4+; "
                    "RSSI/channel still work. +10-20% confidence if signed."
                ),
            })
        else:
            rows.append({
                "name": "CoreWLAN signing",
                "status": "ok",
                "unlocks": "BSSID/RSSI accessible (signing OK)",
            })
    except Exception:
        rows.append({
            "name": "CoreWLAN signing",
            "status": "err",
            "unlocks": "install with `pip install ai-internet-diagnostic-agent[macos]`",
        })
    # log show non-sudo path (D-PRIV-04).
    if shutil.which("log"):
        rows.append({
            "name": "log show (non-sudo)",
            "status": "ok",
            "unlocks": (
                "association/roam events. WIFI_DIAG_USE_WDUTIL=1 unlocks "
                "+15% EAP detail."
            ),
        })
    else:
        rows.append({
            "name": "log show (non-sudo)",
            "status": "err",
            "unlocks": "macOS `log` command not found",
        })
    return rows


def _check_linux() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # NetworkManager via dbus-next (Pitfall 12 — distro fragmentation).
    try:
        import dbus_next  # type: ignore[import-not-found]  # noqa: F401
        nm_present = shutil.which("nmcli") is not None
        if not nm_present:
            try:
                rc = subprocess.run(
                    ["systemctl", "list-units", "--type=service",
                     "NetworkManager.service"],
                    capture_output=True, timeout=5,
                ).returncode
                nm_present = rc == 0
            except (FileNotFoundError, subprocess.TimeoutExpired):
                nm_present = False
        if nm_present:
            rows.append({
                "name": "NetworkManager D-Bus",
                "status": "ok",
                "unlocks": "+30-40% confidence on Linux enterprise networks",
            })
        else:
            rows.append({
                "name": "NetworkManager D-Bus",
                "status": "warn",
                "unlocks": (
                    "v1 supports NM-based stacks (Ubuntu 22.04+ / Fedora / "
                    "Arch+NM). iwd / systemd-networkd standalone deferred to "
                    "v1.x. Falling back to baseline."
                ),
            })
    except ImportError:
        rows.append({
            "name": "NetworkManager D-Bus",
            "status": "err",
            "unlocks": "install with `pip install ai-internet-diagnostic-agent[linux]`",
        })
    return rows


def _check_current_os() -> list[dict[str, Any]]:
    sys_name = platform.system()
    if sys_name == "Windows":
        return _check_windows()
    if sys_name == "Darwin":
        return _check_macos()
    if sys_name == "Linux":
        return _check_linux()
    return []


def render_doctor_table() -> int:
    """Print the per-OS health table. Returns exit code (0 = all OK; 1 = warn/err).

    Educational tone — does NOT offer to elevate inline.
    """
    console = Console()
    table = Table(title="agent doctor", show_lines=True)
    table.add_column("Data Source", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("What unlocking adds")

    rows: list[dict[str, Any]] = [_check_baseline()]
    rows.extend(_check_current_os())

    worst = "ok"
    for r in rows:
        status = r.get("status", "err")
        glyph = _STATUS_GLYPHS.get(status, "?")
        table.add_row(r["name"], glyph, r.get("unlocks", ""))
        if status == "err":
            worst = "err"
        elif status == "warn" and worst != "err":
            worst = "warn"

    console.print(table)
    return 0 if worst == "ok" else 1
