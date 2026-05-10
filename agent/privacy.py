"""`agent privacy` command — render PRIVACY.md + effective config (PRIV-03).

Prints the on-disk PRIVACY.md content (rendered as Markdown) followed by the
agent's effective configuration: default consent level, history retention
days, model revision pin. Closes with the schema-allowlist field
enumeration so the user can see (verbatim from
``TelemetryFrame.model_fields``) what is collected.
"""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from wifi_diag_schema import TelemetryFrame

DEFAULT_CONSENT = "local"
DEFAULT_RETENTION_DAYS = 90
MODEL_REVISION = "v1.0.0"


def _privacy_md_path() -> Path:
    """PRIVACY.md sits at the agent repo root, above the ``agent/`` package."""
    return Path(__file__).resolve().parent.parent / "PRIVACY.md"


def render_privacy() -> None:
    """Print PRIVACY.md content + effective config + schema allowlist.

    Output is captured by ``test_privacy.py``; required strings:
      - "default consent: local"
      - "90" (history retention days)
      - "v1.0.0" (model revision)
      - every required schema field name (timestamp, os, network_mode,
        rssi_dbm, bssid, ...) — rendered via the per-field bullet list
        AND because PRIVACY.md content includes them.
    """
    console = Console()

    # 1. Render PRIVACY.md content (so the user sees the same doc the
    #    test_privacy_doc.py test asserts on).
    md_path = _privacy_md_path()
    if md_path.exists():
        console.print(Markdown(md_path.read_text(encoding="utf-8")))
    else:
        console.print("[red]PRIVACY.md not found[/red]")

    # 2. Effective config block (test_privacy.py asserts these strings).
    console.print("\n[bold]Effective configuration[/bold]")
    console.print(f"  default consent: {DEFAULT_CONSENT}")
    console.print(f"  history retention: {DEFAULT_RETENTION_DAYS} days")
    console.print(f"  model revision: {MODEL_REVISION}")

    # 3. Field allowlist enumeration (test_privacy.py asserts every required
    #    field name appears in the output).
    console.print(
        "\n[bold]Schema-allowlist fields (TelemetryFrame.model_fields)[/bold]",
    )
    for field_name in TelemetryFrame.model_fields:
        console.print(f"  - {field_name}")
