"""Wave 0 RED-state tests for per-event consent (AGENT-04, D-CONSENT-01..04)."""
from __future__ import annotations

import io

from agent.consent import prompt_consent


def test_default_is_local(monkeypatch):
    # Press Enter -> Default ("1") -> "local"
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert prompt_consent(non_interactive=None) == "local"


def test_consent_flag_skips_prompt():
    # Should not read stdin at all; --consent local short-circuits.
    assert prompt_consent(non_interactive="local") == "local"


def test_consent_flag_redacted_phase4_falls_back_to_local(capsys):
    # D-CONSENT-02: cloud options are disabled in Phase 4.
    # Both --consent redacted and --consent ssid MUST return "local"
    # (NOT "redacted" / "ssid") and print a Phase 5 fallback message.
    # Phase 5 will replace this fallback with real cloud paths.
    assert prompt_consent(non_interactive="redacted") == "local"
    out_redacted = capsys.readouterr().out
    assert "Phase 5" in out_redacted, f"Phase 5 fallback message missing: {out_redacted!r}"

    assert prompt_consent(non_interactive="ssid") == "local"
    out_ssid = capsys.readouterr().out
    assert "Phase 5" in out_ssid, f"Phase 5 fallback message missing: {out_ssid!r}"

    # Sanity: --consent local does NOT print the Phase 5 fallback.
    assert prompt_consent(non_interactive="local") == "local"
    out_local = capsys.readouterr().out
    assert "Phase 5" not in out_local


def test_cloud_options_visibly_disabled(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    prompt_consent(non_interactive=None)
    out = capsys.readouterr().out
    assert "[Phase 5]" in out, f"Phase 5 marker missing from prompt:\n{out}"
    assert "Local" in out or "Locally" in out


def test_choosing_cloud_falls_back_to_local(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
    result = prompt_consent(non_interactive=None)
    assert result == "local"
    out = capsys.readouterr().out
    assert "Phase 5" in out  # the fallback message references Phase 5
