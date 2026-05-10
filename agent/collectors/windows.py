"""Windows WLAN-AutoConfig collector (D-PRIV-03; AGENT-01 [Win]; AGENT-02 [Win]).

Reads ``Microsoft-Windows-WLAN-AutoConfig/Operational`` event log via pywin32
``win32evtlog``. Maps event IDs to the schema's ``AuthEventClass`` Literal
(6 values total — see ``wifi_diag_schema.enums``).

Privacy boundary contract (Pitfall 6):
    Raw event-log XML strings are parsed for ``EventID`` ONLY. The XML is
    NEVER attached to a TelemetryFrame field; ``redact_to_schema`` is the
    single boundary that builds the frame from the collector's payload dict.

Why only WLAN-AutoConfig at v1 (D-PRIV-03):
    - Readable by any user (no admin needed) → matches the dogfood case
      (school / corporate laptops forbid elevation).
    - Surfaces association, roam, and EAP-failure events in the same channel.
    - Security log (admin-only) is deferred to v1.x.

Why only the 6-value AuthEventClass enum:
    - The schema is the privacy + telemetry contract; extending it is a
      coordinated change across schema repo + downstream consumers.
    - The current 6 values cover the high-confidence dogfood signal set.
    - Unmapped IDs degrade safely to ``"none"`` (no string leakage).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

try:
    import win32evtlog  # type: ignore[import-not-found]
except ImportError:
    win32evtlog = None  # type: ignore[assignment]

from wifi_diag_schema import TelemetryFrame

from agent.collectors.base import Collector
from agent.collectors.baseline import collect_baseline
from agent.redaction import redact_to_schema

CHANNEL = "Microsoft-Windows-WLAN-AutoConfig/Operational"

# IMPORTANT: only the 6 schema-defined AuthEventClass values are valid.
# Schema source: ../wifi-diag-schema/src/wifi_diag_schema/enums.py
#   none / 8021x_success / 8021x_fail / radius_timeout / eap_fail / eapol_m3_timeout
#
# Win event IDs documented at:
#   Microsoft Learn — Wireless network protected access (WLAN-AutoConfig events)
# Win 11 24H2 EAP-TLS regression (Microsoft Q&A 2025-26) is captured by the
# 11006 -> eap_fail mapping (Pitfall 10).
_EVENT_ID_TO_AUTH_CLASS: dict[int, str] = {
    8001: "8021x_success",   # Wireless security started -> success-track
    8002: "8021x_success",   # Wireless security succeeded
    8003: "8021x_fail",      # Wireless security failed (generic)
    11006: "eap_fail",       # Explicit EAP failure received
    11010: "8021x_fail",     # Association failed (re-uses 8021x_fail; schema
                             # has no "association_fail" enum at v1)
    # 11005 (association attempt) and 12013 (profile mismatch) are
    # intentionally NOT mapped — they have no clean equivalents in the
    # current schema. They become "none" via dict.get(...).
}


def to_auth_event_class(event_id: int) -> str:
    """Map a Windows event ID to the schema's AuthEventClass enum.

    Returns ``"none"`` for any ID we don't ship a mapping for. NEVER returns
    a free-text string — the redaction boundary depends on this contract.
    """
    return _EVENT_ID_TO_AUTH_CLASS.get(event_id, "none")


def _parse_event_id(xml_str: str) -> int:
    """Parse only ``System/EventID``. Discards every other attribute.

    The xml_str is NOT retained beyond this function — caller drops the
    reference immediately. Pitfall 6: raw event-log strings never reach
    a TelemetryFrame field.
    """
    try:
        root = ET.fromstring(xml_str)
        ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
        elem = root.find("./e:System/e:EventID", ns)
        if elem is None:
            # Try without namespace (some event-log dialects).
            elem = root.find("./System/EventID")
        return int(elem.text) if elem is not None and elem.text else -1
    except (ET.ParseError, ValueError, AttributeError):
        return -1


def query_recent_events(seconds_back: int = 30) -> list[tuple[int, str]]:
    """Return ``[(event_id, xml_str), ...]`` from the last N seconds.

    Returns empty list if pywin32 is not installed (non-Windows or skipped
    install). Caller MUST extract specific fields and discard ``xml_str``
    rather than holding onto it (Pitfall 6).
    """
    if win32evtlog is None:
        return []
    flags = win32evtlog.EvtQueryReverseDirection
    xpath = (
        f"*[System[TimeCreated[timediff(@SystemTime) <= {seconds_back * 1000}]]]"
    )
    try:
        handle = win32evtlog.EvtQuery(CHANNEL, flags, xpath, None)
    except Exception:
        return []
    events: list[tuple[int, str]] = []
    while True:
        try:
            batch = win32evtlog.EvtNext(handle, 100, -1, 0)
        except Exception:
            break
        if not batch:
            break
        for evt in batch:
            xml_str = win32evtlog.EvtRender(evt, win32evtlog.EvtRenderEventXml)
            event_id = _parse_event_id(xml_str)
            if event_id > 0:
                events.append((event_id, xml_str))
    return events


class WindowsCollector(Collector):
    """Windows-specific collector. Only constructed by ``make_collector()``
    on Windows (or in tests via mocked ``win32evtlog``)."""

    def __init__(self) -> None:
        self._last_auth_class: str = "none"

    def sample(self) -> TelemetryFrame:
        payload = collect_baseline()
        payload["os"] = "windows"

        # Read recent WLAN-AutoConfig events; map most-recent to AuthEventClass.
        # The xml_str variable is intentionally local-only and dropped on
        # function return (Pitfall 6 — no XML escapes the function frame).
        most_recent_class = self._last_auth_class
        for event_id, _xml_str in query_recent_events(seconds_back=10):
            cls = to_auth_event_class(event_id)
            if cls != "none":
                most_recent_class = cls
                break
        self._last_auth_class = most_recent_class
        payload["auth_event_class"] = most_recent_class

        # Single redaction boundary — schema-allowlist enforced here.
        return redact_to_schema(payload)
