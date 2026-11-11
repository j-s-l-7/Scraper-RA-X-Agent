# ruff: noqa: E501, RUF013
"""``ideation report research`` and ``ideation holdout unseal|status``."""

from __future__ import annotations

from typing import Annotated

import typer

report_app = typer.Typer(no_args_is_help=True, help="Research reports (read-only over stored results).")
holdout_app = typer.Typer(no_args_is_help=True, help="Holdout seal (irreversible unseal needs --yes).")


@report_app.command("research")
def research_cmd(
    family: Annotated[list[str], typer.Option("--family", help="Repeat: 1Y-june, tactical-3M.")] = None,  # type: ignore[assignment]
    holdout: Annotated[bool, typer.Option("--holdout", help="Report the unsealed holdout (refuses while sealed).")] = False,
    as_of: Annotated[str, typer.Option(help="Output folder date (default: today).")] = "",
    snap: Annotated[str, typer.Option()] = "",
) -> None:
    """Write IDEATION_DATA_DIR/reports/<date>/RESEARCH_OOS.md and RANKING_MECHANISM.md (copied to docs/)."""
    from rich.console import Console

    from ideation.cli import _init
    from ideation.report.research import ReportRefused, build

    console = Console(width=200, highlight=False)
    settings = _init()
    fams = family or ["1Y-june", "tactical-3M"]
    try:
        build(settings, fams, snap=snap or None, as_of=as_of or None, log=lambda m: console.print(m), holdout_report=holdout)
    except ReportRefused as e:
        console.print(f"[red]refused:[/red] {e}")
        raise typer.Exit(2) from e


@holdout_app.command("status")
def status_cmd() -> None:
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.report import store

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        h = store.holdout_status(conn)
    typer.echo("unsealed: " + (", ".join(f"{k} at {v['unsealed_at']}" for k, v in h.items()) if h else "none (all sealed)"))


@holdout_app.command("unseal")
def unseal_cmd(
    family: Annotated[str, typer.Option(help="1Y-june | tactical-3M")],
    champion: Annotated[int, typer.Option(help="trial_id of the frozen champion (the trial the owner freezes)")],
    baseline: Annotated[int | None, typer.Option(help="trial_id of the frozen baseline")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Required: irreversible, owner-confirmed.")] = False,
) -> None:
    """IRREVERSIBLE: unseal the holdout of ``family``. Writes the one holdout_unseal row."""
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.report.holdout import UnsealRefused, unseal

    settings = _init()
    try:
        with Writer.from_settings(settings).connect() as conn:
            r = unseal(conn, family, champion, baseline, yes=yes)
    except UnsealRefused as e:
        typer.echo(f"refused: {e}")
        raise typer.Exit(2) from e
    typer.echo(f"unsealed {r}")
