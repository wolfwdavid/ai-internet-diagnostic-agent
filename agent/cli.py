"""Typer CLI surface (D-AGENT-03 — 8 commands).

start / stop / pause / status / diagnose / doctor / privacy / show-telemetry

Pause = sampling-only stop; the daemon process stays alive.
show-telemetry = redacted preview of recent buffer (transparency-by-default).
"""
from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from agent import buffer, process
from agent.consent import ConsentLevel, prompt_consent

app = typer.Typer(
    name="agent",
    help="AI Internet Diagnostic — local Wi-Fi telemetry agent.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def start() -> None:
    """Launch the background daemon."""
    console.print(process.start_daemon())


@app.command()
def stop() -> None:
    """Stop the background daemon."""
    console.print(process.stop_daemon())


@app.command()
def pause() -> None:
    """Pause sampling (daemon stays alive)."""
    console.print(process.pause_daemon())


@app.command()
def status() -> None:
    """Show daemon status + count of undiagnosed flagged drops (D-AGENT-02)."""
    s = process.daemon_status()
    drops = buffer.list_flagged_drops(only_undiagnosed=True)
    if s["running"]:
        uptime = s["uptime_s"] if s["uptime_s"] is not None else 0.0
        console.print(
            f"[green]daemon running[/green] pid={s['pid']} "
            f"uptime={uptime:.0f}s paused={s['paused']}"
        )
    else:
        console.print("[red]daemon not running[/red]")
    console.print(f"undiagnosed drops in buffer: {len(drops)}")
    if drops:
        most_recent = drops[0]
        console.print(
            f"  last drop: ts={most_recent['ts']:.0f} reason={most_recent['reason']}"
        )


@app.command()
def diagnose(
    consent: str | None = typer.Option(
        None,
        "--consent",
        help="Skip interactive prompt: local | redacted | ssid (D-CONSENT-04)",
    ),
) -> None:
    """Diagnose the most-recent flagged drop (or live snapshot if none)."""
    if consent is not None and consent not in ("local", "redacted", "ssid"):
        raise typer.BadParameter(
            f"--consent must be one of local|redacted|ssid (got {consent!r})"
        )
    chosen: ConsentLevel = prompt_consent(non_interactive=consent)  # type: ignore[arg-type]
    result = _run_diagnosis(chosen)
    console.print(result)


def _run_diagnosis(consent: ConsentLevel) -> str:
    """Local-only inference (AGENT-05) + history persistence (AGENT-07)."""
    from agent import history, inference

    # 1. Snapshot recent buffer (D-AGENT-01 — last 120s rolling buffer).
    window = buffer.snapshot_recent(120)
    if not window:
        return (
            "no telemetry in buffer — has the daemon been running? "
            "Run `agent start` first."
        )

    # 2. Local-only inference. Phase 4 ships only consent=local; redacted/ssid
    # are visibly-disabled in the consent prompt and prompt_consent() returns
    # "local" if the user picks a cloud option (D-CONSENT-02 Phase-4 fallback).
    if consent != "local":
        return (
            f"consent={consent} is not wired in Phase 4 (cloud transport ships "
            "in Phase 5); falling back to local-only."
        )

    try:
        verdict = inference.run_local_inference(window)
    except Exception as exc:  # noqa: BLE001 — surface any inference failure
        return f"inference failed: {exc}"

    # 3. Persist to history (D-HISTORY-01 — Verdict + telemetry window for Reality Anchor).
    diag_id = history.write_diagnosis(
        verdict=verdict, telemetry_window=window, consent_level=consent
    )

    # 4. Mark the most-recent flagged drop as diagnosed (D-AGENT-02).
    flagged = buffer.list_flagged_drops(only_undiagnosed=True)
    if flagged:
        buffer.mark_diagnosed(flagged[0]["id"])

    return (
        f"verdict #{diag_id}: {verdict.headline}\n  fix: {verdict.suggested_fix}"
    )


@app.command()
def doctor() -> None:
    """Per-OS health check (D-PRIV-02)."""
    from agent.doctor import render_doctor_table

    exit_code = render_doctor_table()
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command()
def privacy() -> None:
    """Print PRIVACY.md content + effective config (PRIV-03)."""
    from agent.privacy import render_privacy

    render_privacy()


@app.command(name="show-telemetry")
def show_telemetry(
    format: str = typer.Option("table", "--format", help="table | json"),
) -> None:
    """Preview the redacted buffer contents (transparency-by-default per D-AGENT-03)."""
    frames = buffer.snapshot_recent(120)
    if format == "json":
        console.print_json(
            json.dumps([f.model_dump(mode="json") for f in frames], default=str)
        )
        return
    table = Table(title=f"buffer snapshot — {len(frames)} frames (last 120s)")
    table.add_column("ts")
    table.add_column("os")
    table.add_column("rssi_dbm")
    table.add_column("bssid (hashed)")
    for f in frames[-20:]:  # last 20 in table view
        table.add_row(
            f"{f.timestamp:.0f}",
            str(f.os),
            str(getattr(f, "rssi_dbm", "")),
            str(getattr(f, "bssid", ""))[:16] + "...",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# `agent history` subcommand group (D-HISTORY-01..04)
# ---------------------------------------------------------------------------
history_app = typer.Typer(
    name="history",
    help="Local diagnosis history (D-HISTORY-01..04).",
    no_args_is_help=True,
)
app.add_typer(history_app, name="history")


@history_app.command("list")
def history_list(
    limit: int = typer.Option(20, "--limit", help="Max rows to show"),
) -> None:
    """List recent diagnoses (most recent first)."""
    from agent import history as history_mod

    rows = history_mod.list_diagnoses(limit=limit)
    if not rows:
        console.print("[dim]no diagnoses yet[/dim]")
        return
    table = Table(title=f"recent diagnoses (last {len(rows)})")
    table.add_column("id")
    table.add_column("ts")
    table.add_column("schema")
    table.add_column("consent")
    for r in rows:
        table.add_row(
            str(r["id"]),
            f"{r['ts']:.0f}",
            r["schema_version"],
            r["consent_level"],
        )
    console.print(table)


@history_app.command("show")
def history_show(diag_id: int) -> None:
    """Print the full Verdict JSON for a past diagnosis."""
    from agent import history as history_mod

    full = history_mod.show_diagnosis(diag_id)
    if full is None:
        console.print(f"[red]no diagnosis with id={diag_id}[/red]")
        raise typer.Exit(code=1)
    console.print_json(full["verdict_json"])


@history_app.command("clear")
def history_clear(
    keep_last: int = typer.Option(
        0, "--keep-last", help="Keep the most-recent N rows"
    ),
    confirm: bool = typer.Option(
        False, "--confirm", help="Skip the interactive confirmation"
    ),
) -> None:
    """Delete history rows (with optional retention of the most recent N)."""
    from agent import history as history_mod

    if not confirm:
        from rich.prompt import Confirm

        prompt_text = (
            f"Delete all but {keep_last} most-recent diagnoses?"
            if keep_last
            else "Delete ALL diagnosis history?"
        )
        if not Confirm.ask(prompt_text, default=False):
            console.print("aborted")
            raise typer.Exit(code=1)
    n = history_mod.clear(keep_last=keep_last if keep_last > 0 else None)
    console.print(f"deleted {n} rows")


@history_app.command("retention")
def history_retention(
    days: int = typer.Argument(..., help="Retention period in days (D-HISTORY-02)"),
) -> None:
    """Set the history retention period (default 90 days)."""
    from agent import history as history_mod

    history_mod.set_retention_days(days)
    console.print(f"retention set to {days} days")
