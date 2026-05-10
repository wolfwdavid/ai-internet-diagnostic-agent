"""PRIVACY.md is written FROM the schema allowlist (PRIV-02)."""
from __future__ import annotations

from pathlib import Path

from wifi_diag_schema import TelemetryFrame


def test_privacy_md_exists_and_references_schema():
    # The agent repo's PRIVACY.md must reference the schema allowlist verbatim.
    p = Path(__file__).resolve().parent.parent / "PRIVACY.md"
    assert p.exists(), f"PRIVACY.md not found at {p}"
    content = p.read_text(encoding="utf-8")
    assert "schema-allowlist" in content or "schema allowlist" in content, (
        "PRIVACY.md must reference the schema-allowlist privacy contract"
    )
    # Each TelemetryFrame field must appear at least once
    for field in TelemetryFrame.model_fields:
        assert field in content, (
            f"PRIVACY.md is missing reference to schema field {field!r}"
        )
