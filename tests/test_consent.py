"""Per-event consent tests (AGENT-04, D-CONSENT-01..04).

Phase 5 (D-CONSENT-02 cloud-now-live):
  - The ``[Phase 5]`` annotation markers are gone (Gotcha 10).
  - --consent redacted / ssid are honored verbatim (no fallback to local).
  - Choosing options 2 / 3 interactively returns 'redacted' / 'ssid'.
  - Default-on-Enter is still 'local'.
"""
from __future__ import annotations

import io

from agent.consent import prompt_consent


def test_default_is_local(monkeypatch):
    # Press Enter -> Default ("1") -> "local"
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert prompt_consent(non_interactive=None) == "local"


def test_consent_flag_local_skips_prompt():
    # Should not read stdin at all; --consent local short-circuits.
    assert prompt_consent(non_interactive="local") == "local"


def test_consent_flag_cloud_levels_honored():
    """Phase 5: --consent redacted / ssid are honored verbatim (the CLI
    threads them into stream_diagnose). No fallback-to-local."""
    assert prompt_consent(non_interactive="redacted") == "redacted"
    assert prompt_consent(non_interactive="ssid") == "ssid"


def test_prompt_has_no_phase5_annotations(monkeypatch, capsys):
    """Gotcha 10: the ``[Phase 5]`` annotation markers Phase 4 carried as
    removable seeds must be gone."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    prompt_consent(non_interactive=None)
    out = capsys.readouterr().out
    assert "[Phase 5]" not in out, (
        f"Phase 5 marker still present in prompt:\n{out}"
    )
    assert "Local" in out or "Locally" in out


def test_choosing_cloud_returns_cloud_level(monkeypatch):
    """Phase 5: picking option 2 returns 'redacted' (not 'local')."""
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
    assert prompt_consent(non_interactive=None) == "redacted"


def test_choosing_ssid_returns_ssid_level(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("3\n"))
    assert prompt_consent(non_interactive=None) == "ssid"
