"""Phase 1 smoke: package imports + schema dependency resolves."""
from __future__ import annotations


def test_agent_imports():
    import agent

    assert agent.__version__ == "0.1.0"


def test_schema_dep_resolves():
    """D-13: agent pins wifi-diag-schema; verify it imports.

    `[tool.uv.sources]` resolves it from the sibling `../wifi-diag-schema/`
    during local dev. CI will swap to PyPI install once published.
    """
    from wifi_diag_schema import SCHEMA_VERSION, TelemetryFrame

    assert SCHEMA_VERSION == "1.0.0"
    assert TelemetryFrame.model_config["extra"] == "forbid"


def test_handshake_dep_resolves():
    """01-02 added the handshake API; agent will use this in Phase 5 SSE transport."""
    from wifi_diag_schema import (
        HandshakeFrame,
        IncompatibleSchemaError,
        check_compatibility,
        make_handshake,
    )

    h = make_handshake()
    assert h.frame_type == "handshake"
    assert check_compatibility("1.0.0", "1.0.5") == "match"
    # Sanity: major mismatch raises
    import pytest

    with pytest.raises(IncompatibleSchemaError):
        check_compatibility("1.0.0", "2.0.0")
    # HandshakeFrame is the public class
    assert HandshakeFrame is not None
