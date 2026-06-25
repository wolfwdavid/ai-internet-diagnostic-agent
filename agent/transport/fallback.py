"""Auto-fallback to local-only after tenacity exhausts (D-LIVE-04).

When ``agent/transport/client._connect_and_submit`` exhausts its 3 retries on a
``TransientTransportError``, tenacity surfaces a ``RetryError``. The CLI
``diagnose --cloud`` command catches it, prints the amber LOCAL_FALLBACK_BANNER
string, then calls ``fallback_to_local`` on the same buffer snapshot to get a
local verdict. The user always gets a diagnosis; live is the upgrade path,
local is the floor.

Reuses Phase 4's vendored predict_verdict pipeline (``agent.inference``) so
the local fallback verdict is byte-for-byte identical to what ``agent diagnose``
(without --cloud) would have produced.
"""

from __future__ import annotations

from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.verdict import Verdict

from agent.inference import run_local_inference

LOCAL_FALLBACK_BANNER = "[Local mode] verdict computed on owner's laptop (Space was sleeping)."


def fallback_to_local(frames: list[TelemetryFrame]) -> Verdict:
    """Run Phase 4's local-only pipeline on the buffer snapshot.

    Returns the same ``Verdict`` shape ``agent diagnose`` would have produced
    without --cloud. The caller (CLI) is responsible for printing the banner
    and the JSON-dumped verdict.
    """
    return run_local_inference(frames)


__all__ = ["LOCAL_FALLBACK_BANNER", "fallback_to_local"]
