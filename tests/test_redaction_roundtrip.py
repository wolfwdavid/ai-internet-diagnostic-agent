"""CI GATE: hypothesis-based property test for PII roundtrip.

Failing this test is a HARD CI fail per Roadmap Phase 4 success criterion #2.
The TelemetryFrame schema (`extra="forbid"`) IS the privacy contract — this
test exercises adversarial PII payloads to prove no string survives the
redaction boundary into the serialized frame.
"""

from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wifi_diag_schema import TelemetryFrame

from agent.redaction import DENY_PATTERNS, bssid_hash, redact_to_schema

PII_PAYLOADS = st.fixed_dictionaries(
    {
        "evt_xml": st.sampled_from(
            [
                '<EventData><Data Name="Identity">student@school.edu</Data></EventData>',
                (
                    '<EventData><Data Name="Reason">RADIUS_TIMEOUT</Data>'
                    '<Data Name="UserCert">CN=John Doe,OU=Students</Data></EventData>'
                ),
                (
                    '<EventData><Data Name="EapMethod">25</Data>'
                    '<Data Name="Password">hunter2</Data></EventData>'
                ),
                (
                    '<EventData><Data Name="Identity">teacher@school.edu</Data>'
                    '<Data Name="MAC">aa:bb:cc:dd:ee:ff</Data></EventData>'
                ),
                '<EventData><Data Name="CertSubject">CN=root.example.com,O=ACME</Data></EventData>',
            ]
        ),
        "ts": st.floats(
            min_value=1700000000.0, max_value=2000000000.0, allow_nan=False, allow_infinity=False
        ),
        "rssi": st.integers(min_value=-95, max_value=-30),
        "os": st.sampled_from(["windows", "macos", "linux"]),
        "network_mode": st.sampled_from(["enterprise", "captive", "home", "unknown"]),
        "raw_bssid": st.sampled_from(["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]),
    }
)


@given(PII_PAYLOADS)
@settings(
    max_examples=200,
    deadline=None,
    # tmp_state_dir is intentionally shared across hypothesis examples — the
    # per-install salt being deterministic across the 200 examples is the
    # intended behavior (and matches production: one salt per install).
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_redaction_strips_all_pii(tmp_state_dir, payload):
    frame = redact_to_schema(payload)
    assert isinstance(frame, TelemetryFrame)
    dump = frame.model_dump_json()
    for pat in DENY_PATTERNS:
        assert not pat.search(dump), f"PII leaked: pattern {pat.pattern!r} matched in {dump!r}"


def test_extra_keys_rejected_by_schema(tmp_state_dir):
    # raw_message is NOT in the schema allowlist; it must be dropped before validation,
    # OR pydantic must raise ValidationError if it slips through to model_validate.
    payload = {
        "raw_message": "secret stuff",
        "ts": 1700000000.0,
        "rssi": -60,
        "os": "windows",
        "network_mode": "enterprise",
        "raw_bssid": "aa:bb:cc:dd:ee:ff",
        "evt_xml": "<EventData/>",
    }
    frame = redact_to_schema(payload)
    # Either the field doesn't exist on the model OR it's not in fields_set
    assert "raw_message" not in TelemetryFrame.model_fields
    assert "raw_message" not in frame.model_fields_set


def test_bssid_hash_matches_schema_regex(tmp_state_dir):
    h = bssid_hash("aa:bb:cc:dd:ee:ff")
    assert h is not None
    assert re.fullmatch(r"^[0-9a-f]{64}$", h), (
        f"bssid_hash returned {h!r}, expected 64-char lowercase hex"
    )


def test_bssid_hash_deterministic(tmp_state_dir):
    h1 = bssid_hash("aa:bb:cc:dd:ee:ff")
    h2 = bssid_hash("aa:bb:cc:dd:ee:ff")
    assert h1 == h2
    # Different MAC -> different hash
    h3 = bssid_hash("11:22:33:44:55:66")
    assert h3 != h1


def test_bssid_hash_none_passthrough(tmp_state_dir):
    assert bssid_hash(None) is None
