"""Per-event consent gate (D-CONSENT-01..04).

Three options: Local (default) / Share-redacted / Share+SSID. All three are
live and selectable; the cloud levels thread into ``agent diagnose --cloud``
for SSE upload to the Space. Default-on-Enter is still Local; per-event
semantics unchanged (no session memory, no config-file remember-my-choice).

The CLI wires the chosen level into ``agent diagnose --cloud`` for the cloud
transport (D-LIVE-02/03); local stays the local-only path (AGENT-05).
"""
from __future__ import annotations

from typing import Literal

from rich.console import Console
from rich.prompt import Prompt

ConsentLevel = Literal["local", "redacted", "ssid"]

_PROMPT_BODY = (
    "\n[bold]Diagnose this drop?[/bold]\n"
    "  [green][1] Locally[/green]              "
    "— verdict computed on this laptop, nothing leaves\n"
    "  [2] Cloud (redacted)        "
    "— anonymized fingerprint  [recommended for live tab]\n"
    "  [3] Cloud (with SSID)       "
    "— adds your network name\n"
    "Default: [bold]\\[1] Locally[/bold]"
)

_CHOICE_TO_LEVEL: dict[str, ConsentLevel] = {
    "1": "local",
    "2": "redacted",
    "3": "ssid",
}


def prompt_consent(non_interactive: ConsentLevel | None = None) -> ConsentLevel:
    """Per-event consent prompt. D-CONSENT-04 escape: pass non_interactive to skip stdin.

    Phase 5 (D-CONSENT-02 cloud-now-live):
      - All three levels are valid return values.
      - ``--consent redacted`` and ``--consent ssid`` are honored verbatim and
        the CLI threads them into ``stream_diagnose``.
    """
    if non_interactive is not None:
        if non_interactive not in ("local", "redacted", "ssid"):
            raise ValueError(f"invalid consent level: {non_interactive!r}")
        return non_interactive

    console = Console()
    console.print(_PROMPT_BODY)
    choice = Prompt.ask(
        "Choice", choices=["1", "2", "3"], default="1", show_default=False
    )
    return _CHOICE_TO_LEVEL[choice]
