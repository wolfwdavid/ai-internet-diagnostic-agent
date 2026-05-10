"""macOS collector — pyobjc-framework-CoreWLAN + `log show` (D-PRIV-04).

AGENT-01 [macOS] + AGENT-02 [macOS]. Pitfall 11 mitigation:
Sonoma 14.4+ ad-hoc-signed Python returns ``None`` for ``bssid()``/``ssid()``;
this collector detects that, emits a degraded frame (sentinel-hashed BSSID,
schema-valid) instead of crashing. ``agent doctor`` (plan 04-05) surfaces the
"what unlocking adds" message — the collector itself never offers to elevate.

D-PRIV-04 default code path: ``log show --predicate 'subsystem == "com.apple.wifi"'``
(non-sudo). ``WIFI_DIAG_USE_WDUTIL=1`` env var is the ONLY opt-in to the sudo
``wdutil`` path — there is no CLI flag (keeps the privacy posture clean).

Per ARCHITECTURE.md Anti-Pattern 6, this collector shares NOTHING with the
Windows or Linux collectors beyond the schema. Every emission goes through
``redact_to_schema`` (plan 04-05) — the single privacy boundary.
"""
from __future__ import annotations

import json
import os
import subprocess

# Lazy import — pyobjc only available on macOS, and only when the [macos]
# extra is installed. Tests on Win/Linux runners patch CoreWLAN into sys.modules.
try:
    from CoreWLAN import CWInterface  # type: ignore[import-not-found]
except ImportError:
    CWInterface = None  # type: ignore[assignment]

from wifi_diag_schema import TelemetryFrame

from agent.collectors.base import Collector
from agent.collectors.baseline import collect_baseline
from agent.redaction import bssid_hash, redact_to_schema

# ---------------------------------------------------------------------------
# Log-line keyword -> AuthEventClass (canonical schema enum, 6 values).
# Order matters: more specific patterns FIRST so the EAPOL m3-timeout signal
# is not swallowed by a generic "EAPOL" match.
# ---------------------------------------------------------------------------
_LOG_KEYWORD_MAP: list[tuple[str, str]] = [
    ("EAPOL: 4-way handshake failure", "eapol_m3_timeout"),
    ("EAP authentication failed", "eap_fail"),
    ("EAPOL", "eap_fail"),
    ("RADIUS timeout", "radius_timeout"),
    ("Authentication failed", "8021x_fail"),
    ("Association complete", "8021x_success"),
]


def _classify_log_line(message: str) -> str:
    """Map a single ``log show`` ``eventMessage`` to one of the 6 schema enum values."""
    msg_lower = message.lower()
    for needle, cls in _LOG_KEYWORD_MAP:
        if needle.lower() in msg_lower:
            return cls
    return "none"


# Sentinel for ad-hoc-signed-Python None-BSSID case (Pitfall 11).
# Distinct from real BSSIDs because it hashes a fixed labelled string;
# NOT an empty hash (which would collide with empty inputs from other collectors).
# The label is deterministic per install, so repeated None-BSSID frames produce
# the SAME hash — `agent doctor` can detect the constant-hash signal and surface
# the Location-Services / code-signing remediation.
_DEGRADED_BSSID_SENTINEL_INPUT = "wifi-diag:macos-bssid-unavailable-adhoc-signed"


def _query_log_show(window_seconds: int = 30) -> list[str]:
    """Run ``log show`` (default, non-sudo) or ``wdutil`` (opt-in via env var).

    Returns the captured stdout lines (one ndjson record per line). On any
    subprocess error returns an empty list — the agent degrades to a frame
    with ``auth_event_class="none"`` rather than failing the daemon tick.
    """
    if os.environ.get("WIFI_DIAG_USE_WDUTIL") == "1":
        cmd = ["sudo", "wdutil", "log", "+wifi", "+eapol"]
    else:
        cmd = [
            "log", "show",
            "--predicate", 'subsystem == "com.apple.wifi" OR subsystem == "com.apple.eapol"',
            "--info", "--debug",
            "--last", f"{window_seconds}s",
            "--style", "ndjson",
        ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    return [line for line in (out.stdout or "").splitlines() if line.strip()]


def _most_recent_event_class(log_lines: list[str]) -> str:
    """Walk log lines newest -> oldest; return first non-'none' classification.

    ndjson records are emitted in chronological order by ``log show``; the most
    recent disconnect-relevant event is the one we want to surface.
    """
    for line in reversed(log_lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = str(rec.get("eventMessage", ""))
        cls = _classify_log_line(msg)
        if cls != "none":
            return cls
    return "none"


def _get_bssid_safely() -> tuple[str | None, bool]:
    """Return ``(raw_mac_or_None, signing_ok)`` — Pitfall 11.

    On the signed-Python path with Location Services granted, ``CWInterface.bssid()``
    returns the AP MAC. On ad-hoc-signed Python (Sonoma 14.4+) it returns ``None``.
    """
    if CWInterface is None:
        return None, False
    try:
        iface = CWInterface.sharedInstance()
        raw = iface.bssid()
        if raw is None:
            # Sonoma 14.4+ ad-hoc-signed-Python case OR Location Services off.
            return None, False
        return str(raw), True
    except Exception:
        return None, False


def _get_rssi_safely() -> int | None:
    """Return the current RSSI in dBm, or ``None`` if CoreWLAN is unreachable."""
    if CWInterface is None:
        return None
    try:
        iface = CWInterface.sharedInstance()
        r = iface.rssiValue()
        return int(r) if r is not None else None
    except Exception:
        return None


class MacOSCollector(Collector):
    """macOS-specific collector. Only constructed by ``make_collector()`` on Darwin.

    Per ARCHITECTURE.md Anti-Pattern 6: this class shares NOTHING with the
    Windows or Linux collectors beyond the ``Collector`` ABC and the privacy
    boundary (``redact_to_schema``). No cross-platform Wi-Fi shim.
    """

    def __init__(self) -> None:
        self._signing_warned: bool = False

    def sample(self) -> TelemetryFrame:
        """Take one sample tick: baseline + CoreWLAN BSSID/RSSI + log show events."""
        payload = collect_baseline()
        payload["os"] = "macos"

        # CoreWLAN: BSSID (with Pitfall 11 fallback) and RSSI.
        raw_bssid, signing_ok = _get_bssid_safely()
        if raw_bssid is None:
            # Degraded-not-broken: hash a sentinel string so the schema-required
            # `bssid` field is populated AND the same sentinel produces the same
            # hash deterministically (avoids "all-None-rows-collide-randomly"
            # surfaced by Pitfall 2 — the constant-hash signal IS the doctor flag).
            payload["bssid"] = bssid_hash(_DEGRADED_BSSID_SENTINEL_INPUT)
            payload["bssid_mode"] = "hashed"
        else:
            # redact_to_schema will hash raw_bssid -> bssid (hashed mode).
            payload["raw_bssid"] = raw_bssid
        rssi = _get_rssi_safely()
        if rssi is not None:
            payload["rssi_dbm"] = rssi

        # log show (or wdutil opt-in) -> auth_event_class enum.
        log_lines = _query_log_show(window_seconds=30)
        payload["auth_event_class"] = _most_recent_event_class(log_lines)

        # Single redaction boundary — Pitfall 6 made structural.
        # Non-allowlist keys (raw_bssid, ping_*_ms, iface_*) are dropped here.
        return redact_to_schema(payload)
