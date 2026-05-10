"""Wave 0 RED-state tests for the Typer CLI surface (AGENT-03)."""
from __future__ import annotations

from typer.testing import CliRunner

from agent.cli import app

runner = CliRunner()


def test_help_lists_8_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "start",
        "stop",
        "pause",
        "status",
        "diagnose",
        "doctor",
        "privacy",
        "show-telemetry",
    ):
        assert cmd in result.output, f"Missing command in --help: {cmd}"


def test_diagnose_consent_flag_accepts_local(tmp_state_dir, mocker):
    # Mock the actual diagnosis pipeline so this test only exercises the flag plumbing.
    mocker.patch("agent.cli._run_diagnosis", return_value="ok")
    result = runner.invoke(app, ["diagnose", "--consent", "local"])
    assert result.exit_code == 0, result.output


def test_diagnose_consent_flag_rejects_unknown_value():
    result = runner.invoke(app, ["diagnose", "--consent", "foo"])
    assert result.exit_code != 0
    assert "consent" in result.output.lower() or "Invalid value" in result.output
