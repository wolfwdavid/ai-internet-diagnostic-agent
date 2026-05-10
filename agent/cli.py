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
    """Phase 4 stub. Plan 04-06 wires the actual inference pipeline.

    For now, returns a status-only message so Task 1's CLI test
    (`test_diagnose_consent_flag_accepts_local`) can pass with a mocker patch.
    """
    window = buffer.snapshot_recent(120)
    return (
        f"diagnose stub: consent={consent} window_size={len(window)} "
        "(inference wiring lands in plan 04-06)"
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
