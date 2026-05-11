"""Phase 5 plan 05-04: E2E sleep-mid-stream test (Pitfall 5 mitigation verification).

Mocks ``agent.transport.client._connect_and_submit`` to inject a drop-after-frame-5,
then verifies ``stream_diagnose()`` recovers via the ``last_acked_ts`` cursor
and completes the diagnosis with NO duplicate or lost frames.

Runs in the standard pytest tests/phase05 suite -- no real Space required.
The real-Space E2E lives in ``.github/workflows/e2e-sleep-mid-stream.yml``
(``workflow_dispatch``-only) and exercises the same agent code path.

Test sequence (per plan 05-04):
  1. Construct 10 TelemetryFrames with timestamps 1.0 .. 10.0.
  2. First ``stream_diagnose`` call: scripted fake Space yields ``handshake_ok``
     + 5 ``state=streaming`` chunks (frame_index 1..5), then the fake Job
     raises TransientTransportError mid-iteration.
  3. Cursor must record ``last_acked_ts == 5.0``.
  4. Second ``stream_diagnose`` call (the retry): scripted fake Space yields
     ``handshake_ok`` + 5 ``state=streaming`` chunks (frame_index 1..5 of the
     5-frame replay submission) + ``state=computing`` + ``state=complete``.
  5. Cursor must reach ``last_acked_ts == 10.0`` -- ZERO TELEMETRY LOSS.
  6. The two ``client.submit`` calls received DIFFERENT frame lists (10, then 5).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.telemetry import PingContinuity

from agent.transport import client as client_mod
from agent.transport.client import _CLIENT_CACHE, stream_diagnose
from agent.transport.errors import TransientTransportError
from agent.transport.replay import load_last_acked_ts, reset_cursor

FIXTURES = Path(__file__).parent / "fixtures"


def _make_frame(ts: float) -> TelemetryFrame:
    """Minimal valid TelemetryFrame; mirrors tests/phase05/test_transport.py::_frame."""
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


def _scripted_job(yields: list[dict], raise_at_end: bool = False):
    """Build a fake Job whose iteration yields the scripted chunks.

    If ``raise_at_end`` is True, raise TransientTransportError AFTER yielding
    all scripted chunks (simulating a mid-stream connection drop after the
    Space has yielded the chunks it managed to compute pre-sleep).
    """
    class FakeJob:
        session_hash = "fake-session-abc"

        def __iter__(self):
            for c in yields:
                yield c
            if raise_at_end:
                raise TransientTransportError("simulated sleep mid-stream")

    return FakeJob()


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """Ensure the module-level Client cache is empty between tests."""
    _CLIENT_CACHE.clear()
    yield
    _CLIENT_CACHE.clear()


def test_sleep_midstream_replays_from_cursor(mocker, tmp_cache_dir):
    """End-to-end: cursor advance to 5.0, replay, complete -> cursor 10.0."""
    reset_cursor()

    fixtures = json.loads((FIXTURES / "fake_space_yields.json").read_text())
    first_yields = fixtures["first_run_yields_before_drop"]
    retry_yields = fixtures["retry_yields_after_wake"]

    frames = [_make_frame(ts=float(i)) for i in range(1, 11)]  # ts = 1..10

    submit_calls: list[list] = []
    call_seq = {"n": 0}

    def fake_connect_and_submit(space_id, handshake_json, frames_json_list,
                                 owner_key, pair_code):
        submit_calls.append(list(frames_json_list))
        n = call_seq["n"]
        call_seq["n"] += 1
        client = MagicMock(session_hash="fake-session-abc")
        if n == 0:
            # first_yields = handshake_ok + 5 streaming = 6 chunks; raise after.
            return client, _scripted_job(first_yields, raise_at_end=True)
        return client, _scripted_job(retry_yields, raise_at_end=False)

    mocker.patch.object(client_mod, "_connect_and_submit",
                        side_effect=fake_connect_and_submit)

    # First invocation -- should yield 5 streaming chunks then raise.
    chunks_a: list[dict] = []
    with pytest.raises(TransientTransportError):
        for c in stream_diagnose("fake/space", frames, owner_key="OWNER"):
            chunks_a.append(c)

    assert sum(1 for c in chunks_a if c.get("state") == "streaming") == 5
    cursor_after_drop = load_last_acked_ts()
    assert cursor_after_drop["last_acked_ts"] == 5.0, (
        f"cursor must record last successful ack = 5.0, got {cursor_after_drop}"
    )

    # Second invocation -- replays from ts > 5.0; 5 streaming + complete.
    chunks_b: list[dict] = list(
        stream_diagnose("fake/space", frames, owner_key="OWNER")
    )

    assert any(c.get("state") == "complete" for c in chunks_b), (
        "second invocation must reach state=complete"
    )
    assert sum(1 for c in chunks_b if c.get("state") == "streaming") == 5

    # CRITICAL: cursor advanced to the highest ts after full diagnosis.
    final_cursor = load_last_acked_ts()
    assert final_cursor["last_acked_ts"] == 10.0, (
        f"cursor must reach last_acked_ts 10.0 after full diagnosis, "
        f"got {final_cursor}"
    )

    # CRITICAL: the second call sent only 5 frames (the unacked tail).
    assert len(submit_calls) == 2, (
        f"expected 2 submit calls, got {len(submit_calls)}"
    )
    assert len(submit_calls[0]) == 10, "first call should send all 10 frames"
    assert len(submit_calls[1]) == 5, (
        "second call should send only the 5 unacked frames"
    )

    # CRITICAL: the second call sends exactly ts = {6, 7, 8, 9, 10}.
    second_ts = {json.loads(f)["timestamp"] for f in submit_calls[1]}
    assert second_ts == {6.0, 7.0, 8.0, 9.0, 10.0}, (
        f"second call should send exactly ts=6..10 (unacked tail), got {second_ts}"
    )


def test_handshake_sent_each_invocation(mocker, tmp_cache_dir):
    """Each stream_diagnose call re-sends the handshake JSON.

    One Gradio submit = one handshake (mirror of the wire protocol).
    """
    reset_cursor()
    handshakes_received: list[str] = []

    def fake_connect_and_submit(space_id, handshake_json, frames_json_list,
                                 owner_key, pair_code):
        handshakes_received.append(handshake_json)
        client = MagicMock(session_hash="abc")
        return client, _scripted_job([
            {"state": "handshake_ok", "schema_version": "1.1.0"},
            {"state": "complete", "verdict": {}},
        ])

    mocker.patch.object(client_mod, "_connect_and_submit",
                        side_effect=fake_connect_and_submit)

    frames = [_make_frame(ts=1.0)]
    list(stream_diagnose("fake/space", frames, owner_key="OWNER"))
    # Bump the cursor so the second call has unacked frames (avoid empty submit
    # short-circuit if implementation ever adds one).
    list(stream_diagnose("fake/space", [_make_frame(ts=2.0)],
                          owner_key="OWNER"))

    assert len(handshakes_received) == 2, (
        f"expected 2 handshakes (one per invocation), got {len(handshakes_received)}"
    )
    # Both handshakes should be valid HandshakeFrame JSON.
    for hs in handshakes_received:
        parsed = json.loads(hs)
        assert parsed["frame_type"] == "handshake", (
            f"handshake JSON missing frame_type=handshake: {parsed}"
        )
        assert "schema_version" in parsed, (
            f"handshake JSON missing schema_version: {parsed}"
        )


def test_no_telemetry_lost_end_to_end(mocker, tmp_cache_dir):
    """The union of submitted frames across all invocations must equal the
    input set: every frame ts=1..10 must be submitted at least once.
    """
    reset_cursor()

    fixtures = json.loads((FIXTURES / "fake_space_yields.json").read_text())
    first_yields = fixtures["first_run_yields_before_drop"]
    retry_yields = fixtures["retry_yields_after_wake"]
    frames = [_make_frame(ts=float(i)) for i in range(1, 11)]

    call_seq = {"n": 0}
    submitted_ts: list[set[float]] = []

    def fake_connect_and_submit(space_id, handshake_json, frames_json_list,
                                 owner_key, pair_code):
        submitted_ts.append(
            {json.loads(f)["timestamp"] for f in frames_json_list}
        )
        n = call_seq["n"]
        call_seq["n"] += 1
        client = MagicMock(session_hash="abc")
        if n == 0:
            return client, _scripted_job(first_yields, raise_at_end=True)
        return client, _scripted_job(retry_yields, raise_at_end=False)

    mocker.patch.object(client_mod, "_connect_and_submit",
                        side_effect=fake_connect_and_submit)

    with pytest.raises(TransientTransportError):
        list(stream_diagnose("fake/space", frames, owner_key="OWNER"))
    list(stream_diagnose("fake/space", frames, owner_key="OWNER"))

    # All 10 timestamps must appear in the union of submitted sets.
    total_seen: set[float] = set().union(*submitted_ts)
    expected = {float(i) for i in range(1, 11)}
    missing = expected - total_seen
    assert not missing, (
        f"every frame ts=1..10 must have been submitted at least once; "
        f"missing {missing}"
    )
    # And the union should be exactly {1..10} -- no spurious extras.
    assert total_seen == expected, (
        f"submitted timestamps must equal input set; got {total_seen}"
    )

    # And the cursor confirms zero loss at the end (last_acked_ts == 10.0).
    final = load_last_acked_ts()
    assert final["last_acked_ts"] == 10.0, (
        f"cursor must reach last_acked_ts 10.0 after full diagnosis, got {final}"
    )
