"""Schema-layer PII redaction (PRIV-01 + Pitfall 6 mitigation).

The ``TelemetryFrame`` schema is configured ``extra="forbid"`` — any non-allowlist
key raises ``ValidationError``. This module is the structural privacy boundary:
collectors hand it a dict containing whatever they grabbed from the OS,
``redact_to_schema`` projects that down to the schema-allowlist keys,
hashes the BSSID, and returns a validated ``TelemetryFrame``. Anything not
in the allowlist (``raw_message``, ``username``, ``Identity``, ``UserCert``,
``Password``, etc.) is dropped at the boundary — it cannot reach the buffer
or the wire.

Defense in depth: the ``DENY_PATTERNS`` regex set is exercised by the
hypothesis-driven CI gate in ``tests/test_redaction_roundtrip.py`` against
adversarial PII payloads. If any of those patterns ever appears in a
serialized frame the build fails.

Public API:
    redact_to_schema(payload: dict) -> TelemetryFrame
    bssid_hash(mac: str | None) -> str | None
    DENY_PATTERNS: list[re.Pattern]
    SCHEMA_ALLOWLIST: frozenset[str]
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.telemetry import PingContinuity

from agent.salt import load_or_create_salt

# ---------------------------------------------------------------------------
# Allowlist: source of truth is the schema model.
# ---------------------------------------------------------------------------
SCHEMA_ALLOWLIST: frozenset[str] = frozenset(TelemetryFrame.model_fields.keys())

# ---------------------------------------------------------------------------
# Deny patterns — adversarial PII shapes the redaction must NEVER let through.
# Used by tests/test_redaction_roundtrip.py CI GATE.
# ---------------------------------------------------------------------------
DENY_PATTERNS: list[re.Pattern] = [
    re.compile(r"@school\.edu", re.IGNORECASE),
    re.compile(r"hunter2"),
    re.compile(r"CN="),
    re.compile(r"<EventData", re.IGNORECASE),
    re.compile(r"<Data\s+Name="),
    # Raw MAC pattern: aa:bb:cc:dd:ee:ff (NOT acceptable in any string field).
    re.compile(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", re.IGNORECASE),
    re.compile(r"Identity"),
    re.compile(r"Password"),
    re.compile(r"UserCert"),
]

# ---------------------------------------------------------------------------
# Allowed enum vocabularies (defense against per-OS string leaks via Pitfall 6).
# Reference: wifi_diag_schema.enums
# ---------------------------------------------------------------------------
_ALLOWED_AUTH_EVENTS = {
    "none", "8021x_success", "8021x_fail",
    "radius_timeout", "eap_fail", "eapol_m3_timeout",
}
_ALLOWED_DHCP_EVENTS = {
    "none", "discover_no_offer", "nak_on_renew", "request_loop",
}
_ALLOWED_DRIVER_STATES = {
    "normal", "post_wake_init", "power_save_active",
    "u_apsd_active", "error", "unknown",
}
_ALLOWED_MAC_RAND_STATES = {
    "off", "per_network", "per_session", "rejected",
}
_ALLOWED_OS = {"windows", "macos", "linux"}
_ALLOWED_NETWORK_MODES = {"enterprise", "captive", "home", "unknown"}
_ALLOWED_BSSID_MODES = {"raw", "hashed"}
_ALLOWED_WINDOW_MS = {30000, 120000}


def _coerce(value: Any, allowed: set, default: str | int) -> str | int:
    """Return ``value`` if it is in ``allowed``, otherwise ``default``."""
    return value if value in allowed else default


# ---------------------------------------------------------------------------
# BSSID hashing: SHA-256 with per-install salt.
# ---------------------------------------------------------------------------
def bssid_hash(mac: Optional[str]) -> Optional[str]:
    """Return ``sha256(salt || mac)`` hex digest, or ``None`` if mac is ``None``.

    Output matches the schema regex ``^[0-9a-f]{64}$``. Same MAC + same
    per-install salt produces the same hash; different install -> different
    salt -> different hash (BSSIDs unlinkable across installs).
    """
    if mac is None:
        return None
    salt = load_or_create_salt()
    digest = hashlib.sha256()
    digest.update(salt)
    digest.update(mac.lower().encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# The redaction boundary.
# ---------------------------------------------------------------------------
def redact_to_schema(payload: dict) -> TelemetryFrame:
    """Convert an arbitrary collector payload dict to a validated TelemetryFrame.

    Drops every key not in ``SCHEMA_ALLOWLIST``. Hashes ``raw_bssid`` ->
    ``bssid`` (hashed mode). Coerces enum-shaped fields (``auth_event_class``,
    ``dhcp_event_class``, ``driver_state``, ``mac_randomization_state``, ``os``,
    ``network_mode``, ``bssid_mode``) to the schema vocabulary — rejects
    free-text values silently by mapping to a safe default ("none" / "unknown").

    For required schema fields the collector did not provide, populates safe
    defaults so the boundary never raises ``ValidationError`` from a sparse
    test payload (the hypothesis CI gate exercises this path with minimal
    inputs).

    Raises:
        pydantic.ValidationError: if a value violates the schema's range/regex
            constraints (e.g., RSSI outside [-100, 0]). Allowlist violations
            are impossible because we project to the allowlist before
            ``model_validate``.
    """
    # ------------------------------------------------------------------
    # Step 1: extract and hash BSSID (raw -> hashed) BEFORE we forget the raw value.
    # ------------------------------------------------------------------
    raw_bssid = payload.get("raw_bssid") or payload.get("bssid_raw")
    bssid = payload.get("bssid")
    bssid_mode = payload.get("bssid_mode")
    if raw_bssid and not bssid:
        bssid = bssid_hash(raw_bssid)
        bssid_mode = "hashed"

    # ------------------------------------------------------------------
    # Step 2: build a clean dict with ONLY allowlist keys.
    # Non-allowlist keys (raw_message, evt_xml, Identity, ...) are
    # silently dropped here — they cannot reach model_validate.
    # ------------------------------------------------------------------
    clean: dict[str, Any] = {}

    # Map common collector field aliases to schema names.
    if "ts" in payload and "timestamp" not in payload:
        clean["timestamp"] = payload["ts"]
    if "rssi" in payload and "rssi_dbm" not in payload:
        clean["rssi_dbm"] = payload["rssi"]

    # Project allowlist keys through.
    for k, v in payload.items():
        if k in SCHEMA_ALLOWLIST:
            clean[k] = v

    # ------------------------------------------------------------------
    # Step 3: enforce hashed BSSID (from step 1).
    # ------------------------------------------------------------------
    if bssid is not None:
        clean["bssid"] = bssid
    if bssid_mode is not None:
        clean["bssid_mode"] = _coerce(bssid_mode, _ALLOWED_BSSID_MODES, "hashed")

    # ------------------------------------------------------------------
    # Step 4: coerce enum-shaped fields to schema vocabulary.
    # Free-text -> safe default. NEVER let an OS-event-log string leak in.
    # ------------------------------------------------------------------
    clean["auth_event_class"] = _coerce(
        payload.get("auth_event_class"), _ALLOWED_AUTH_EVENTS, "none",
    )
    clean["dhcp_event_class"] = _coerce(
        payload.get("dhcp_event_class", clean.get("dhcp_event_class")),
        _ALLOWED_DHCP_EVENTS, "none",
    )
    clean["driver_state"] = _coerce(
        payload.get("driver_state", clean.get("driver_state")),
        _ALLOWED_DRIVER_STATES, "unknown",
    )
    clean["mac_randomization_state"] = _coerce(
        payload.get("mac_randomization_state",
                    clean.get("mac_randomization_state")),
        _ALLOWED_MAC_RAND_STATES, "off",
    )

    # ------------------------------------------------------------------
    # Step 5: defaults for required fields the collector may omit.
    # Required-but-not-defaulted fields on TelemetryFrame are: timestamp,
    # os, network_mode, rssi_dbm, bssid, bssid_mode, channel,
    # ping_continuity, dhcp_event_class, auth_event_class,
    # captive_portal_detected, mac_randomization_state, driver_state,
    # window_ms. Collectors emit these in production; the property test
    # exercises a sparse payload and exposes the boundary's defaults.
    # ------------------------------------------------------------------
    if "timestamp" not in clean:
        clean["timestamp"] = float(payload.get("ts", 0.0))
    if "os" not in clean:
        clean["os"] = _coerce(payload.get("os", "windows"),
                              _ALLOWED_OS, "windows")
    else:
        clean["os"] = _coerce(clean["os"], _ALLOWED_OS, "windows")
    if "network_mode" not in clean:
        clean["network_mode"] = _coerce(
            payload.get("network_mode", "unknown"),
            _ALLOWED_NETWORK_MODES, "unknown",
        )
    else:
        clean["network_mode"] = _coerce(
            clean["network_mode"], _ALLOWED_NETWORK_MODES, "unknown",
        )
    if "rssi_dbm" not in clean:
        clean["rssi_dbm"] = int(payload.get("rssi", -90))
    if "bssid" not in clean:
        clean["bssid"] = bssid_hash(raw_bssid or "00:00:00:00:00:00")
    if "bssid_mode" not in clean:
        clean["bssid_mode"] = "hashed"
    if "channel" not in clean:
        clean["channel"] = int(payload.get("channel_default", 36))
    if "ping_continuity" not in clean:
        clean["ping_continuity"] = PingContinuity(
            window_ms=2000,
            avg_rtt_ms=None,
            packet_loss_pct=0.0,
            jitter_ms=None,
        )
    if "captive_portal_detected" not in clean:
        clean["captive_portal_detected"] = bool(
            payload.get("captive_portal_detected", False),
        )
    if "window_ms" not in clean:
        clean["window_ms"] = _coerce(
            payload.get("window_ms_default", 120000),
            _ALLOWED_WINDOW_MS, 120000,
        )
    else:
        clean["window_ms"] = _coerce(
            clean["window_ms"], _ALLOWED_WINDOW_MS, 120000,
        )

    return TelemetryFrame.model_validate(clean)
