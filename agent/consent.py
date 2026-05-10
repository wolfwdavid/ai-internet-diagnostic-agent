"""Per-event consent gate (D-CONSENT-01..04).

Three options: Local (default) / Share-redacted [Phase 5] / Share+SSID [Phase 5].
Cloud options are visibly DISABLED in Phase 4 with `[Phase 5]` annotation; choosing
one falls back to Local with a one-line explanation. Default-on-Enter is Local.
No session memory — every diagnose invocation re-prompts (D-CONSENT-04 rejects config files).
"""
from __future__ import annotations

from typing import Literal, Optional

from rich.console import Console
from rich.prompt import Prompt

ConsentLevel = Literal["local", "redacted", "ssid"]

_PROMPT_BODY = (
    "\n[bold]Diagnose this drop?[/bold]\n"
    "  [green][1] Locally[/green]              "
    "— verdict computed on this laptop, nothing leaves\n"
    "  [dim][2] Cloud (redacted)[/dim]    "
    "— anonymized fingerprint  [dim italic][Phase 5][/dim italic]\n"
    "  [dim][3] Cloud (with SSID)[/dim]  "
    "— adds your network name  [dim italic][Phase 5][/dim italic]\n"
    "Default: [bold]\\[1] Locally[/bold]"
)


def prompt_consent(non_interactive: Optional[ConsentLevel] = None) -> ConsentLevel:
    """Per-event consent prompt. D-CONSENT-04 escape: pass non_interactive to skip stdin.

    D-CONSENT-02 (Phase 4): cloud options are disabled. The non-interactive
    path must enforce the SAME disabled-options policy as the interactive path
    — passing --consent redacted or --consent ssid in Phase 4 falls back to
    "local" with a one-line Phase 5 message. Phase 5 will replace this
    fallback with the real cloud transport paths.
    """
    if non_interactive is not None:
        if non_interactive not in ("local", "redacted", "ssid"):
            raise ValueError(f"invalid consent level: {non_interactive!r}")
        if non_interactive in ("redacted", "ssid"):
            # D-CONSENT-02 Phase 4 guard — DO NOT bypass via the flag.
            # Phase 5 will replace this branch with the real cloud paths.
            console = Console()
            console.print(
                "[yellow][Phase 5] Cloud diagnosis ships in Phase 5; "
                "falling back to local-only.[/yellow]"
            )
            return "local"
        return non_interactive

    console = Console()
    console.print(_PROMPT_BODY)
    choice = Prompt.ask(
        "Choice", choices=["1", "2", "3"], default="1", show_default=False
    )
    if choice in ("2", "3"):
        console.print(
            "[yellow][Phase 5] Cloud diagnosis ships in Phase 5; "
            "falling back to local-only.[/yellow]"
        )
        return "local"
    return "local"
