"""Windows WLAN-AutoConfig collector tests — runs on every OS via mocked pywin32.

Per CONTEXT D-PRIV-03 the v1 Windows event-log scope is
``Microsoft-Windows-WLAN-AutoConfig/Operational`` only (no Security log, no
System log; both admin-only or admin-recommended). These tests assert:

1. The 6-value AuthEventClass enum mapping holds for the known IDs we ship
   (8001/8002/8003/11006/11010).
2. Unknown event IDs degrade safely to ``"none"`` (no enum extension).
3. The channel literal is the documented WLAN-AutoConfig channel.
4. ``redact_to_schema`` (plan 04-05) is the single boundary — no raw XML
   substring escapes through ``WindowsCollector.sample().model_dump_json()``.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


CANNED_EVENT_8003_XML = """<?xml version="1.0" encoding="utf-8"?>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <Provider Name="Microsoft-Windows-WLAN-AutoConfig"/>
    <EventID>8003</EventID>
    <TimeCreated SystemTime="2026-05-10T14:00:00.000Z"/>
  </System>
  <EventData>
    <Data Name="Reason">5</Data>
  </EventData>
</Event>"""

CANNED_EVENT_8001_XML = CANNED_EVENT_8003_XML.replace(
    "<EventID>8003</EventID>", "<EventID>8001</EventID>",
)
CANNED_EVENT_11006_XML = CANNED_EVENT_8003_XML.replace(
    "<EventID>8003</EventID>", "<EventID>11006</EventID>",
)
CANNED_EVENT_99999_XML = CANNED_EVENT_8003_XML.replace(
    "<EventID>8003</EventID>", "<EventID>99999</EventID>",
)


@pytest.fixture
def mock_win32evtlog(mocker):
    """Install a fake ``win32evtlog`` module so the collector module imports cleanly
    on every OS — pywin32 isn't installed on macOS/Linux CI runners."""
    fake = MagicMock()
    fake.EvtQuery.return_value = "fake-handle"
    fake.EvtQueryReverseDirection = 0x2
    fake.EvtRenderEventXml = 1
    # Default: return one event then empty.
    fake.EvtNext.side_effect = [["evt-handle"], []]
    fake.EvtRender.return_value = CANNED_EVENT_8003_XML
    mocker.patch.dict(sys.modules, {"win32evtlog": fake})
    # Force re-import of the collector module so it picks up the fake.
    sys.modules.pop("agent.collectors.windows", None)
    return fake


def test_parses_event_8003_to_eap_fail_or_8021x_fail(mock_win32evtlog):
    from agent.collectors.windows import to_auth_event_class

    result = to_auth_event_class(8003)
    # Schema enum values: none / 8021x_success / 8021x_fail / radius_timeout / eap_fail / eapol_m3_timeout.
    # Either "eap_fail" or "8021x_fail" is acceptable for "Wireless security failed".
    assert result in ("eap_fail", "8021x_fail"), (
        f"Event 8003 mapped to {result!r}, expected eap_fail or 8021x_fail"
    )


def test_parses_event_8001_to_8021x_success_or_none(mock_win32evtlog):
    from agent.collectors.windows import to_auth_event_class

    result = to_auth_event_class(8001)
    # 8001 = "Wireless security started" — schema has no "started"; accept 8021x_success or none.
    assert result in ("8021x_success", "none"), (
        f"Event 8001 mapped to {result!r}"
    )


def test_parses_event_11006_to_eap_fail(mock_win32evtlog):
    from agent.collectors.windows import to_auth_event_class

    result = to_auth_event_class(11006)
    assert result == "eap_fail", (
        f"Event 11006 (Explicit EAP failure) must map to eap_fail, got {result!r}"
    )


def test_unknown_event_id_returns_none(mock_win32evtlog):
    from agent.collectors.windows import to_auth_event_class

    assert to_auth_event_class(99999) == "none"


def test_collector_uses_wlan_autoconfig_channel(mock_win32evtlog):
    from agent.collectors.windows import CHANNEL, query_recent_events

    assert CHANNEL == "Microsoft-Windows-WLAN-AutoConfig/Operational"
    query_recent_events(seconds_back=30)
    call_args, _ = mock_win32evtlog.EvtQuery.call_args
    assert "Microsoft-Windows-WLAN-AutoConfig/Operational" in str(call_args)


def test_no_raw_xml_in_emitted_frame(tmp_state_dir, mock_win32evtlog, mocker):
    """Pitfall 6 / privacy boundary: collector emits a TelemetryFrame whose
    JSON serialization contains NO raw XML substrings from the event log."""
    fake_psutil = mocker.patch("agent.collectors.baseline.psutil")
    fake_psutil.net_if_stats.return_value = {
        "Wi-Fi": MagicMock(isup=True, speed=300, mtu=1500),
    }
    fake_psutil.net_io_counters.return_value = {
        "Wi-Fi": MagicMock(
            bytes_sent=0, bytes_recv=0, errin=0, errout=0, dropin=0, dropout=0,
        ),
    }
    fake_icmp = mocker.patch("agent.collectors.baseline.icmplib")
    fake_host = MagicMock(
        min_rtt=10, avg_rtt=12, max_rtt=15, packet_loss=0.0, jitter=1.0,
    )
    fake_icmp.ping.return_value = fake_host

    from agent.collectors.windows import WindowsCollector

    collector = WindowsCollector()
    frame = collector.sample()
    dumped = frame.model_dump_json()
    # No raw XML tags or attribute substrings may leak through the redaction boundary.
    assert "<EventData" not in dumped
    assert "<Event " not in dumped
    assert "<Data Name=" not in dumped
    assert "Microsoft-Windows-WLAN-AutoConfig" not in dumped
