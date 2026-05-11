"""Client-side handshake emission (Phase 5 plan 05-02).

Wraps ``wifi_diag_schema.handshake.make_handshake()`` to JSON for the first arg
of the Space's ``live_diagnose`` endpoint. Centralized so future capability
flags (per D-05 minor-add semantics) have one place to land.
"""
from __future__ import annotations

from wifi_diag_schema.handshake import HandshakeFrame, make_handshake


def build_handshake_json(capabilities: list[str] | None = None) -> str:
    """Return a JSON-serialized HandshakeFrame stamped with the local SCHEMA_VERSION."""
    return make_handshake(capabilities=capabilities).model_dump_json()


__all__ = ["build_handshake_json", "HandshakeFrame", "make_handshake"]
