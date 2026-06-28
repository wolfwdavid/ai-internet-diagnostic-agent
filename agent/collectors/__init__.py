"""Per-OS collector dispatch (Anti-Pattern 6 — no cross-platform shim).

The dispatcher imports the OS-specific module ONLY on its OS. macOS/Linux
modules land via plans 04-03 / 04-04; both imports are wrapped in lazy
try/except so plan 04-02 can ship Windows-first without depending on the
sibling waves' completion.

Public API:
    make_collector() -> Collector

Fallback contract:
    If a per-OS collector module fails to import (e.g., missing per-OS
    extras like pywin32 on a Windows runner that didn't install
    ``[windows]``), ``_BaselineOnlyCollector`` returns the cross-OS
    baseline payload through the redaction boundary.
"""

from __future__ import annotations

import platform

from agent.collectors.base import Collector


def make_collector() -> Collector:
    """Return the per-OS collector. Imports the OS-specific module ONLY on that OS."""
    sys_name = platform.system()
    if sys_name == "Windows":
        try:
            from agent.collectors.windows import WindowsCollector

            return WindowsCollector()
        except ImportError:
            pass
    if sys_name == "Darwin":
        # Plan 04-03 lands MacOSCollector
        try:
            from agent.collectors.macos import MacOSCollector  # type: ignore[import-not-found]

            return MacOSCollector()
        except ImportError:
            pass
    if sys_name == "Linux":
        # Plan 04-04 lands LinuxCollector
        try:
            from agent.collectors.linux import LinuxCollector  # type: ignore[import-not-found]

            return LinuxCollector()
        except ImportError:
            pass
    # Fallback: baseline-only collector (works without per-OS deps; Pitfall 12).
    return _BaselineOnlyCollector()


class _BaselineOnlyCollector(Collector):
    """Used when a per-OS collector module is missing. Baseline-only fallback
    so the daemon still produces valid TelemetryFrames on unsupported stacks."""

    def sample(self):
        from agent.collectors.baseline import collect_baseline
        from agent.redaction import redact_to_schema

        return redact_to_schema(collect_baseline())
