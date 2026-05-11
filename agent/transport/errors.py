"""Transport error classification (Phase 5 plan 05-02).

TransientTransportError -> retry-eligible (cold-start, network blip).
PermanentTransportError -> fail fast (schema mismatch, 4xx, repo deleted).

The classification is asserted by tests/phase05/test_transport.py against
the observed exception types in agent/transport/EXCEPTION_NOTES.md.
"""
from __future__ import annotations


class TransientTransportError(Exception):
    """Wraps cold-start / network-blip errors that warrant tenacity retry."""


class PermanentTransportError(Exception):
    """Wraps schema-mismatch / 4xx / repo-deleted errors. Do NOT retry."""
