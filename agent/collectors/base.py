"""Collector ABC — sample() -> TelemetryFrame; close().

Per ARCHITECTURE.md Anti-Pattern 6 (no cross-platform Wi-Fi shim): this is the
ONLY shared abstraction between per-OS collectors. The schema (TelemetryFrame)
IS the contract; per-OS branches MUST go through ``agent.redaction.redact_to_schema``
to construct frames.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from wifi_diag_schema import TelemetryFrame


class Collector(ABC):
    """Abstract collector interface — one ``sample()`` call per daemon tick."""

    @abstractmethod
    def sample(self) -> TelemetryFrame:
        """Take one sample tick. Returns a fully-redacted, schema-validated frame."""
        raise NotImplementedError

    def close(self) -> None:
        """Release resources (D-Bus subscriptions, event-log handles, etc.)."""
        return None
