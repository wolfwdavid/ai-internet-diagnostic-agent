"""Cross-OS baseline (psutil + icmplib).

Works on every OS without admin. Provides ~60% of classifier signal even when
per-OS event-log paths are unavailable (e.g., Linux without NetworkManager,
or macOS without CoreLocation grant).

This module produces a payload **dict** (NOT a TelemetryFrame). Per-OS
collectors layer additional fields on top, then hand the merged dict to
``agent.redaction.redact_to_schema`` — the single privacy boundary.
"""
from __future__ import annotations

import platform
import time
from typing import Any

import icmplib
import psutil

PROBE_HOST = "8.8.8.8"
PROBE_COUNT = 1
PROBE_TIMEOUT_S = 1.0

_OS_MAP = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}


def _detect_os() -> str:
    return _OS_MAP.get(platform.system(), "linux")


def _ping_once() -> dict[str, Any]:
    """Best-effort single ICMP probe to ``PROBE_HOST``. Returns degraded
    100% packet-loss payload on any exception (admin gates, network down)."""
    try:
        host = icmplib.ping(
            PROBE_HOST,
            count=PROBE_COUNT,
            timeout=PROBE_TIMEOUT_S,
            privileged=False,
        )
        return {
            "ping_avg_rtt_ms": float(getattr(host, "avg_rtt", 0.0) or 0.0),
            "ping_min_rtt_ms": float(getattr(host, "min_rtt", 0.0) or 0.0),
            "ping_max_rtt_ms": float(getattr(host, "max_rtt", 0.0) or 0.0),
            "ping_packet_loss": float(getattr(host, "packet_loss", 0.0) or 0.0),
            "ping_jitter_ms": float(getattr(host, "jitter", 0.0) or 0.0),
        }
    except Exception:
        return {
            "ping_avg_rtt_ms": 0.0,
            "ping_min_rtt_ms": 0.0,
            "ping_max_rtt_ms": 0.0,
            "ping_packet_loss": 100.0,
            "ping_jitter_ms": 0.0,
        }


def _wifi_iface_stats() -> dict[str, Any]:
    """Pick the first active non-loopback interface as a best-effort proxy
    for the Wi-Fi adapter. Per-OS collectors override the interface choice
    when they have better information (Windows BSSID query, etc.)."""
    try:
        stats = psutil.net_if_stats()
    except Exception:
        stats = {}
    for name, s in stats.items():
        if name.lower() in ("lo", "loopback"):
            continue
        if getattr(s, "isup", False):
            return {
                "iface_name": name,
                "iface_speed_mbps": float(getattr(s, "speed", 0) or 0),
            }
    return {"iface_name": "", "iface_speed_mbps": 0.0}


def collect_baseline() -> dict:
    """Produce a partial payload (NOT yet redacted).

    Caller (per-OS collector or the baseline-only fallback) is responsible
    for handing this dict to ``agent.redaction.redact_to_schema`` to build a
    schema-validated TelemetryFrame. Non-allowlist keys here (``iface_name``,
    ``ping_avg_rtt_ms``, ...) are silently dropped at that boundary.
    """
    return {
        "ts": time.time(),
        "os": _detect_os(),
        "network_mode": "enterprise",   # default; per-OS collectors may override
        "rssi_dbm": -65,                # placeholder; per-OS collectors override
        **_ping_once(),
        **_wifi_iface_stats(),
    }
