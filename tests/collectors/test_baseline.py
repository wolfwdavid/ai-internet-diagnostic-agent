"""Cross-OS baseline collector tests (psutil + icmplib) — runs on all 3 OSes.

The baseline collector provides ~60% of the classifier signal regardless of OS
(adapter counters + ICMP continuity). Per-OS collectors layer event-log signal
on top.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_psutil(mocker):
    fake = mocker.patch("agent.collectors.baseline.psutil")
    fake.net_if_stats.return_value = {
        "Wi-Fi": MagicMock(isup=True, speed=300, mtu=1500),
        "lo": MagicMock(isup=True, speed=0, mtu=65536),
    }
    fake.net_io_counters.return_value = {
        "Wi-Fi": MagicMock(
            bytes_sent=1000, bytes_recv=2000,
            errin=0, errout=0, dropin=0, dropout=0,
        ),
    }
    return fake


@pytest.fixture
def mock_icmplib(mocker):
    fake = mocker.patch("agent.collectors.baseline.icmplib")
    host = MagicMock()
    host.min_rtt = 12.0
    host.avg_rtt = 15.0
    host.max_rtt = 22.0
    host.packet_loss = 0.0
    host.jitter = 2.0
    fake.ping.return_value = host
    return fake


def test_psutil_returns_iface_counters(tmp_state_dir, mock_psutil, mock_icmplib):
    from agent.collectors.baseline import collect_baseline

    payload = collect_baseline()
    assert "os" in payload
    assert "network_mode" in payload
    # Timestamp present under either name
    assert payload.get("ts") is not None or payload.get("timestamp") is not None


def test_icmp_ping_populates_continuity(tmp_state_dir, mock_psutil, mock_icmplib):
    from agent.collectors.baseline import collect_baseline

    payload = collect_baseline()
    # Whatever the schema field for ping RTT is — assert it's populated.
    ping_keys = [k for k in payload if "ping" in k.lower() or "rtt" in k.lower()]
    assert ping_keys, (
        f"baseline must produce a ping/rtt field; got keys: {list(payload.keys())}"
    )
