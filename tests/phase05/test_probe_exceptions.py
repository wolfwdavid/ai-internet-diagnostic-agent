"""Wave-0 PROBE: catalog gradio_client exception types on cold-start / unreachable.

Two probe modes:
  (a) Synthetic: connect to a refused-connection URL. Always runs in CI.
  (b) Live: if ``WIFI_DIAG_PROBE_LIVE=1``, also probes the real (possibly
      sleeping) Space.

Outputs the catalog into ``agent/transport/EXCEPTION_NOTES.md``.

GATES Task 2: the retry-predicate in ``agent/transport/client.py`` MUST treat
every recorded type as TransientTransportError.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from gradio_client import Client

EXCEPTION_NOTES = Path(__file__).resolve().parents[2] / "agent" / "transport" / "EXCEPTION_NOTES.md"


def _observe(fn):
    """Run ``fn``; return (module, type_name, repr) on raise, None on success."""
    try:
        fn()
    except BaseException as e:  # noqa: BLE001 — cataloging the surface is the point
        return (type(e).__module__, type(e).__name__, repr(e)[:200])
    return None


def test_probe_unreachable_url_records_exception_type():
    """Connect to a refused-connection URL and append the exception type."""
    obs = _observe(lambda: Client("http://127.0.0.1:1", verbose=False))
    assert obs is not None, "expected an exception when connecting to refused-connection URL"
    mod, name, repr_ = obs
    EXCEPTION_NOTES.parent.mkdir(parents=True, exist_ok=True)
    with EXCEPTION_NOTES.open("a", encoding="utf-8") as f:
        f.write(
            f"\n## Unreachable URL probe ({time.strftime('%Y-%m-%dT%H:%M:%S')})\n"
            f"- module: `{mod}`\n"
            f"- type: `{name}`\n"
            f"- repr: `{repr_}`\n"
        )


@pytest.mark.skipif(
    os.environ.get("WIFI_DIAG_PROBE_LIVE") != "1",
    reason="set WIFI_DIAG_PROBE_LIVE=1 to probe the live Space",
)
def test_probe_live_space_cold_start():
    """Probe a real Space; record success/failure either way."""
    obs = _observe(lambda: Client("WolfDavid/wifi-diag", verbose=False))
    with EXCEPTION_NOTES.open("a", encoding="utf-8") as f:
        if obs is None:
            f.write(
                f"\n## Live Space probe ({time.strftime('%Y-%m-%dT%H:%M:%S')})\n"
                f"- result: SUCCESS (Space was warm)\n"
            )
        else:
            mod, name, repr_ = obs
            f.write(
                f"\n## Live Space probe ({time.strftime('%Y-%m-%dT%H:%M:%S')})\n"
                f"- module: `{mod}`\n"
                f"- type: `{name}`\n"
                f"- repr: `{repr_}`\n"
            )
