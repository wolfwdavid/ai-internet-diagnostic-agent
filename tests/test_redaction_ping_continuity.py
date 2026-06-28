"""Tests for baseline ping_* -> PingContinuity mapping in agent/redaction.py.

Closes Plan 04-02 SUMMARY line 61 follow-up: baseline measures real RTT /
jitter / packet-loss against 8.8.8.8 on every sample tick, but the existing
``redact_to_schema`` projection drops the flat ``ping_*`` keys (not in
``SCHEMA_ALLOWLIST``) and then null-defaults the ``ping_continuity``
sub-object. Net effect: every ``TelemetryFrame`` on the wire has degraded
ping signal even when the real network is healthy.

These tests document the RED -> GREEN contract for the new Step 4b mapping
block in ``redact_to_schema`` (insert between the existing Step 4 enum
coercion and the Step 5 defaults block).
"""

from __future__ import annotations

from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.telemetry import PingContinuity

from agent.redaction import redact_to_schema


def _base_payload() -> dict:
    """Minimal required-field payload (mirrors PII_PAYLOADS shape from the
    roundtrip CI gate). Tests overlay ping_* keys on top."""
    return {
        "ts": 1700000000.0,
        "rssi": -60,
        "os": "windows",
        "network_mode": "enterprise",
        "raw_bssid": "aa:bb:cc:dd:ee:ff",
    }


def test_baseline_ping_keys_map_to_ping_continuity(tmp_state_dir):
    """Happy path: flat ping_avg_rtt_ms / ping_jitter_ms / ping_packet_loss
    keys (as emitted by ``baseline.collect_baseline()``) flow into the
    resulting ``frame.ping_continuity`` sub-object — NOT the hard-coded
    null default."""
    payload = _base_payload()
    payload["ping_avg_rtt_ms"] = 12.3
    payload["ping_jitter_ms"] = 1.5
    payload["ping_packet_loss"] = 0.0

    frame = redact_to_schema(payload)

    assert isinstance(frame, TelemetryFrame)
    assert frame.ping_continuity.avg_rtt_ms == 12.3
    assert frame.ping_continuity.jitter_ms == 1.5
    assert frame.ping_continuity.packet_loss_pct == 0.0
    # window_ms = 1000 matches PROBE_TIMEOUT_S = 1.0 in baseline.py
    # (single-probe RTT, not a multi-probe windowed aggregate).
    assert frame.ping_continuity.window_ms == 1000


def test_baseline_network_down_branch_emits_none_rtt(tmp_state_dir):
    """Network-down branch: ``baseline._ping_once()`` returns
    ``ping_packet_loss=100.0`` plus 0.0 RTT / jitter on any exception.

    In a 100%-loss state, a 0.0 RTT measurement is semantically
    meaningless — the PingContinuity field docstring says ``avg_rtt_ms``
    is "None if no probes returned". Surface ``None`` for a cleaner
    downstream classifier signal."""
    payload = _base_payload()
    payload["ping_avg_rtt_ms"] = 0.0
    payload["ping_jitter_ms"] = 0.0
    payload["ping_packet_loss"] = 100.0

    frame = redact_to_schema(payload)

    assert frame.ping_continuity.packet_loss_pct == 100.0
    assert frame.ping_continuity.avg_rtt_ms is None
    assert frame.ping_continuity.jitter_ms is None
    assert frame.ping_continuity.window_ms == 1000


def test_caller_pre_populated_ping_continuity_is_passthrough(tmp_state_dir):
    """When a caller (per-OS collectors in plans 04-03 / 04-04 that may
    build a richer sub-object) places a PingContinuity on the payload
    BEFORE redaction, the new mapping block must NOT clobber it — even
    if flat ping_* keys are also present on the payload."""
    pre_built = PingContinuity(
        window_ms=120000,
        avg_rtt_ms=42.0,
        packet_loss_pct=5.0,
        jitter_ms=3.0,
    )
    payload = _base_payload()
    payload["ping_continuity"] = pre_built
    # Provocative: flat keys with different values; mapping must NOT clobber.
    payload["ping_avg_rtt_ms"] = 999.0
    payload["ping_jitter_ms"] = 999.0
    payload["ping_packet_loss"] = 0.0

    frame = redact_to_schema(payload)

    assert frame.ping_continuity.avg_rtt_ms == 42.0
    assert frame.ping_continuity.jitter_ms == 3.0
    assert frame.ping_continuity.packet_loss_pct == 5.0
    assert frame.ping_continuity.window_ms == 120000


def test_sparse_payload_keeps_step5_null_default(tmp_state_dir):
    """Regression guard: payload with NO ping_* keys (mimics the hypothesis
    PII_PAYLOADS shape) must still get today's Step 5 null default.

    Currently passes on the unfixed redaction.py because Step 5 fires for
    every payload — this is intentional. After Task 2 lands, this test
    still passes because the new Step 4b block does NOT fire when no
    ping_* keys are on the payload (so Step 5 still installs the null
    default and the hypothesis CI gate's behavior is unchanged)."""
    payload = _base_payload()  # No ping_* keys.

    frame = redact_to_schema(payload)

    assert frame.ping_continuity.window_ms == 2000
    assert frame.ping_continuity.avg_rtt_ms is None
    assert frame.ping_continuity.packet_loss_pct == 0.0
    assert frame.ping_continuity.jitter_ms is None


def test_out_of_range_packet_loss_is_clamped(tmp_state_dir):
    """Defensive clamp: out-of-range ``ping_packet_loss`` values (should
    never come from baseline, but the boundary must not raise
    pydantic.ValidationError on ``Field(ge=0.0, le=100.0)``)."""
    # High clamp: 150.0 -> 100.0 (triggers the network-down branch too,
    # so avg / jitter become None).
    payload_high = _base_payload()
    payload_high["ping_avg_rtt_ms"] = 12.0
    payload_high["ping_jitter_ms"] = 1.0
    payload_high["ping_packet_loss"] = 150.0
    frame_high = redact_to_schema(payload_high)
    assert frame_high.ping_continuity.packet_loss_pct == 100.0
    assert frame_high.ping_continuity.avg_rtt_ms is None
    assert frame_high.ping_continuity.jitter_ms is None

    # Low clamp: -5.0 -> 0.0 (healthy branch; RTT / jitter pass through).
    payload_low = _base_payload()
    payload_low["ping_avg_rtt_ms"] = 12.0
    payload_low["ping_jitter_ms"] = 1.0
    payload_low["ping_packet_loss"] = -5.0
    frame_low = redact_to_schema(payload_low)
    assert frame_low.ping_continuity.packet_loss_pct == 0.0
    assert frame_low.ping_continuity.avg_rtt_ms == 12.0
    assert frame_low.ping_continuity.jitter_ms == 1.0

    # Defensive negative RTT / jitter clamped to 0.0
    payload_neg = _base_payload()
    payload_neg["ping_avg_rtt_ms"] = -1.0
    payload_neg["ping_jitter_ms"] = -2.0
    payload_neg["ping_packet_loss"] = 0.0
    frame_neg = redact_to_schema(payload_neg)
    assert frame_neg.ping_continuity.avg_rtt_ms == 0.0
    assert frame_neg.ping_continuity.jitter_ms == 0.0
    assert frame_neg.ping_continuity.packet_loss_pct == 0.0
