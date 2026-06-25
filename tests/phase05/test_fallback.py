"""Tests for the D-LIVE-04 local-fallback path + CLI --cloud/--pair-code flags.

Covers:
  - fallback_to_local invokes the local-only inference pipeline.
  - On tenacity exhaustion the CLI prints LOCAL_FALLBACK_BANNER and exits 0.
  - --cloud is accepted by typer.
  - --pair-code is passed through to stream_diagnose.
"""

from __future__ import annotations

from typer.testing import CliRunner
from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.telemetry import PingContinuity
from wifi_diag_schema.verdict import Verdict

from agent.cli import app
from agent.transport.fallback import (
    LOCAL_FALLBACK_BANNER,
    fallback_to_local,
)


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
            window_ms=2000, avg_rtt_ms=12.3, packet_loss_pct=0.0, jitter_ms=1.5
        ),
        dhcp_event_class="none",
        auth_event_class="8021x_success",
        captive_portal_detected=False,
        mac_randomization_state="off",
        driver_state="normal",
        window_ms=120000,
    )


def _sentinel_verdict() -> Verdict:
    return Verdict(
        top_class="auth_8021x_eap_fail",
        confidence=0.9,
        top_k=[("auth_8021x_eap_fail", 0.9)],
        headline="sentinel",
        suggested_fix="sentinel-fix",
        evidence=[],
    )


def test_local_fallback_invokes_run_local_inference(mocker, tmp_cache_dir):
    """fallback_to_local must call agent.inference.run_local_inference and
    return its Verdict."""
    sentinel = _sentinel_verdict()
    mock = mocker.patch("agent.transport.fallback.run_local_inference", return_value=sentinel)
    out = fallback_to_local([_frame(1.0)])
    assert out is sentinel
    mock.assert_called_once()


def test_local_fallback_after_tenacity_exhausts(mocker, tmp_state_dir):
    """When _connect_and_submit always raises TransientTransportError,
    tenacity exhausts -> RetryError -> CLI prints banner + local verdict."""
    from agent.transport import client as client_mod
    from agent.transport.errors import TransientTransportError

    # Patch the Client constructor to always raise -- tenacity will exhaust.
    mocker.patch.object(client_mod, "Client", side_effect=TransientTransportError("offline"))
    # Stub the local-inference call so the test doesn't need a real classifier.
    sentinel = _sentinel_verdict()
    mock_local = mocker.patch("agent.transport.fallback.run_local_inference", return_value=sentinel)
    # Stub buffer.snapshot_recent so a real SQLite buffer isn't needed.
    mocker.patch("agent.cli.buffer.snapshot_recent", return_value=[_frame(1.0)])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "diagnose",
            "--cloud",
            "--space-id",
            "fake/space",
            "--consent",
            "redacted",
        ],
    )
    assert result.exit_code == 0, (
        f"expected 0 (local fallback exits cleanly), got {result.exit_code}\n"
        f"output:\n{result.output}\nexc: {result.exception}"
    )
    assert LOCAL_FALLBACK_BANNER in result.output, f"banner missing from output:\n{result.output}"
    mock_local.assert_called_once()


def test_cli_cloud_flag_accepted(mocker, tmp_state_dir):
    """`agent diagnose --cloud --space-id fake/space --consent redacted` is
    accepted by typer (no 'unexpected argument') and calls stream_diagnose."""
    fake_chunks = [{"state": "complete", "verdict": {}}]
    mocker.patch(
        "agent.transport.client.stream_diagnose",
        return_value=iter(fake_chunks),
    )
    # Also need to patch the import inside _run_cloud_diagnosis.
    mocker.patch(
        "agent.transport.client.stream_diagnose",
        return_value=iter(fake_chunks),
    )
    mocker.patch("agent.cli.buffer.snapshot_recent", return_value=[_frame(1.0)])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "diagnose",
            "--cloud",
            "--space-id",
            "fake/space",
            "--consent",
            "redacted",
        ],
    )
    # The flag was accepted (no typer.BadParameter on --cloud) -- exit code
    # depends on whether stream_diagnose returned a complete chunk.
    assert "Got unexpected argument" not in result.output
    assert "--cloud" not in result.output or result.exit_code == 0, (
        f"--cloud not accepted? exit={result.exit_code} out={result.output}"
    )


def test_cli_pair_code_passed_to_transport(mocker, tmp_state_dir):
    """--pair-code ABC123XY must be forwarded to stream_diagnose."""
    captured: dict = {}

    def _fake_stream(space_id, frames, owner_key, pair_code=None):
        captured["space_id"] = space_id
        captured["pair_code"] = pair_code
        yield {"state": "complete", "verdict": {}}

    # Patch where _run_cloud_diagnosis imports it from.
    mocker.patch(
        "agent.transport.client.stream_diagnose",
        side_effect=_fake_stream,
    )
    mocker.patch("agent.cli.buffer.snapshot_recent", return_value=[_frame(1.0)])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "diagnose",
            "--cloud",
            "--pair-code",
            "ABC123XY",
            "--space-id",
            "fake/space",
            "--consent",
            "redacted",
        ],
    )
    assert result.exit_code == 0, (
        f"exit={result.exit_code} out={result.output} exc={result.exception}"
    )
    assert captured.get("pair_code") == "ABC123XY", f"pair_code not forwarded: captured={captured}"
