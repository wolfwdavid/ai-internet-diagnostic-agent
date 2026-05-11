"""TDD tests for agent.transport.client (Phase 5 plan 05-02 task 2).

Covers:
  - Tenacity wraps Client construction + submit (NOT job iteration -- Gotcha 2).
  - Retry filter classifies httpx.ConnectError / ReadTimeout as
    TransientTransportError (per EXCEPTION_NOTES.md probe).
  - Schema-mismatch -> PermanentTransportError, no retry.
  - Long-lived Client cache (D-STATUS-10).
  - Cursor advances per state=streaming yield.
  - Acked-frame skipping on reconnect.
  - Job iteration is NOT retry-wrapped (Gotcha 2).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest
from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.telemetry import PingContinuity

from agent.transport import client as client_mod
from agent.transport.client import (
    _CLIENT_CACHE,
    _connect_and_submit,
    stream_diagnose,
)
from agent.transport.errors import (
    PermanentTransportError,
    TransientTransportError,
)
from agent.transport.replay import load_last_acked_ts, save_last_acked_ts


def _frame(ts: float) -> TelemetryFrame:
    return TelemetryFrame(
        timestamp=ts,
        os="windows",
        network_mode="enterprise",
        rssi_dbm=-60,
        bssid="0" * 64,
        bssid_mode="hashed",
        channel=36,
        ping_continuity=PingContinuity(
            window_ms=2000,
            avg_rtt_ms=12.3,
            packet_loss_pct=0.0,
            jitter_ms=1.5,
        ),
        dhcp_event_class="none",
        auth_event_class="8021x_success",
        captive_portal_detected=False,
        mac_randomization_state="off",
        driver_state="normal",
        window_ms=120000,
    )


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """Ensure the module-level Client cache is empty between tests."""
    _CLIENT_CACHE.clear()
    yield
    _CLIENT_CACHE.clear()


def test_tenacity_wraps_submit(mocker, tmp_cache_dir):
    """Client raises httpx.ConnectError twice, succeeds on 3rd attempt."""
    fake_job = MagicMock(name="job")
    fake_client = MagicMock(name="client")
    fake_client.submit.return_value = fake_job

    calls = {"n": 0}

    def _client_ctor(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("refused")
        return fake_client

    mocker.patch.object(client_mod, "Client", side_effect=_client_ctor)
    c, j = _connect_and_submit("fake/space", "{}", [], None, None)
    assert calls["n"] == 3
    assert c is fake_client
    assert j is fake_job


def test_retry_classifies_correctly(mocker, tmp_cache_dir):
    """httpx.ReadTimeout exhausts tenacity -> TransientTransportError."""
    mocker.patch.object(
        client_mod, "Client", side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(TransientTransportError):
        _connect_and_submit("fake/space", "{}", [], None, None)


def test_schema_mismatch_no_retry(mocker, tmp_cache_dir):
    """Exception containing 'schema mismatch' surfaces as PermanentTransportError
    after exactly one attempt (tenacity does NOT retry permanent)."""
    calls = {"n": 0}

    def _client_ctor(*a, **k):
        calls["n"] += 1
        raise RuntimeError("Schema mismatch: agent 0.x vs space 1.x")

    mocker.patch.object(client_mod, "Client", side_effect=_client_ctor)
    with pytest.raises(PermanentTransportError):
        _connect_and_submit("fake/space", "{}", [], None, None)
    assert calls["n"] == 1, f"expected 1 attempt on permanent error, got {calls['n']}"


def test_long_lived_client_reused(mocker, tmp_cache_dir):
    """Two calls with the same space_id construct the Client only once
    (D-STATUS-10 long-lived Client cache)."""
    fake_client = MagicMock(name="client")
    fake_client.submit.return_value = MagicMock(name="job")
    ctor = mocker.patch.object(client_mod, "Client", return_value=fake_client)

    _connect_and_submit("fake/space", "{}", [], None, None)
    _connect_and_submit("fake/space", "{}", [], None, None)
    assert ctor.call_count == 1


def test_stream_diagnose_advances_cursor(mocker, tmp_cache_dir):
    """Per state=streaming yield, cursor advances to the acked frame ts;
    on state=complete, cursor pins to the max ts in to_send."""
    fake_client = MagicMock(name="client")
    chunks = [
        {"state": "handshake_ok", "schema_version": "1.1.0"},
        {"state": "streaming", "frame_index": 1, "total": 2},
        {"state": "streaming", "frame_index": 2, "total": 2},
        {"state": "complete", "verdict": {}},
    ]
    fake_job = iter(chunks)
    # job.__iter__ is needed; iter(chunks) returns a list_iterator that already
    # implements __iter__. Wrap in MagicMock to expose attribute-based mocks.
    fake_job_mock = MagicMock(name="job")
    fake_job_mock.__iter__.return_value = iter(chunks)
    fake_client.submit.return_value = fake_job_mock
    mocker.patch.object(client_mod, "Client", return_value=fake_client)

    frames = [_frame(10.0), _frame(20.0)]
    received = list(stream_diagnose("fake/space", frames, None))
    assert any(c.get("state") == "complete" for c in received)
    final = load_last_acked_ts()
    assert final["last_acked_ts"] == 20.0


def test_stream_diagnose_skips_acked_frames(mocker, tmp_cache_dir):
    """Preset cursor to 15.0; only frame ts=20 should reach Client.submit."""
    save_last_acked_ts(15.0, "prev")
    fake_client = MagicMock(name="client")
    fake_job_mock = MagicMock(name="job")
    fake_job_mock.__iter__.return_value = iter(
        [{"state": "complete", "verdict": {}}]
    )
    fake_client.submit.return_value = fake_job_mock
    mocker.patch.object(client_mod, "Client", return_value=fake_client)

    frames = [_frame(10.0), _frame(15.0), _frame(20.0)]
    list(stream_diagnose("fake/space", frames, None))

    # Inspect what was passed to submit: positional args.
    assert fake_client.submit.called, "submit was not called"
    args, kwargs = fake_client.submit.call_args
    # signature: (handshake_json, frames_json_list, owner_key, pair_code, api_name=...)
    frames_arg = args[1]
    assert len(frames_arg) == 1, (
        f"expected exactly 1 unacked frame to be sent; got {len(frames_arg)}"
    )


def test_no_retry_on_job_iteration(mocker, tmp_cache_dir):
    """Errors raised while iterating the Job propagate WITHOUT tenacity retry.

    If tenacity were retrying the iteration loop, the iterator side_effect
    would be exhausted on the first attempt and the second attempt would hit
    StopIteration. We instead require the exception to surface immediately.
    """
    fake_client = MagicMock(name="client")
    iter_calls = {"n": 0}

    def _raising_iter():
        iter_calls["n"] += 1
        yield {"state": "handshake_ok"}
        raise httpx.ReadTimeout("mid-stream")

    fake_job_mock = MagicMock(name="job")
    fake_job_mock.__iter__.return_value = _raising_iter()
    fake_client.submit.return_value = fake_job_mock
    mocker.patch.object(client_mod, "Client", return_value=fake_client)

    with pytest.raises(httpx.ReadTimeout):
        list(stream_diagnose("fake/space", [_frame(1.0)], None))
    assert iter_calls["n"] == 1, (
        "Job iteration was retried -- Gotcha 2 violated (tenacity must NOT "
        "wrap the for-chunk-in-job loop)"
    )
