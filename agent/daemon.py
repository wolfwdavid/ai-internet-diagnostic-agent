"""Daemon entry point.

Phase 4 baseline: no-op sampling tick. Plans 04-02/03/04 wire per-OS collectors;
plan 04-05 wires redaction; plan 04-06 wires inference. This file establishes the
asyncio loop + paused-marker handshake + buffer-write target.

Per-tick contract for downstream collectors (04-02/03/04):
  1. Check `_paused()` — skip the tick if the daemon was paused via `agent pause`.
  2. Build a TelemetryFrame from the per-OS collector.
  3. Pass it through `agent.redaction.redact_to_schema` (plan 04-05).
  4. Persist via `agent.buffer.append_frame`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import platformdirs

log = logging.getLogger("agent.daemon")

SAMPLING_INTERVAL_S = 1.0  # ICMP cadence; per CONTEXT.md Discretion
RSSI_INTERVAL_S = 5.0  # RSSI poll cadence
BUFFER_PRUNE_INTERVAL_S = 30.0  # call buffer.prune_older_than every 30s


def _paused() -> bool:
    return (Path(platformdirs.user_state_dir("wifi-diag")) / "daemon.paused").exists()


# Module-level lazy singleton — initialized on first tick to keep daemon
# import lightweight and to avoid spurious OS-permission prompts at import time.
_collector_instance = None


def _get_collector():
    """Lazy per-OS collector init. Plan 04-02 wires WindowsCollector;
    plans 04-03/04-04 wire MacOSCollector / LinuxCollector via the same
    ``make_collector`` dispatcher."""
    global _collector_instance
    if _collector_instance is None:
        from agent.collectors import make_collector

        _collector_instance = make_collector()
    return _collector_instance


async def _sampling_tick() -> None:
    """One sampling tick. Logs and continues on collector exceptions — a
    transient OS-event-log read failure must NOT crash the daemon."""
    from agent import buffer

    try:
        collector = _get_collector()
        frame = collector.sample()
        buffer.append_frame(frame)
    except Exception as exc:
        log.warning("sample tick failed: %s", exc)
    await asyncio.sleep(0)


async def main() -> None:
    from agent import buffer  # local import to avoid module-load side effects

    last_prune = 0.0
    while True:
        now = time.time()
        if not _paused():
            await _sampling_tick()
            if now - last_prune > BUFFER_PRUNE_INTERVAL_S:
                buffer.prune_older_than(120)
                last_prune = now
        await asyncio.sleep(SAMPLING_INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
