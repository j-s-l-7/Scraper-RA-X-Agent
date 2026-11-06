"""``ideation trials …``: register the pre-registered trial lists, list them, check a hash before training."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help="Pre-registered trial lists (05 §3.1): register before any fit.")
console = Console()
Family = Annotated[str, typer.Option("--family", help="1Y-june | tactical-3M")]


@app.command("register")
def register_cmd(
    family: Family,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the trials and hashes; write nothing.")] = False,
) -> None:
    """Insert every trial of the family into ideation.experiment_trial (status 'registered'). Idempotent."""
    from ideation.trials.registry import load_family

    specs = load_family(family)
    if dry_run:
        table = Table("num", "id", "kind", "config_hash")
        for s in specs:
            kind = s.config.get("learner") or s.config.get("model") or f"consensus:{s.config['consensus']}"
            table.add_row(str(s.num), s.trial_id, str(kind), s.config_hash[:12])
        console.print(table)
        console.print(f"{family}: {len(specs)} trials (dry run, nothing written)")
        return
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.trials.registry import register

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        res = register(conn, specs)
    new, old = len(res.inserted), len(res.existing)
    console.print(f"{family}: registered {new} new, {old} already present; N = {res.n_family}")


@app.command("list")
def list_cmd(family: Family) -> None:
    """Registered trials of a family with their status."""
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.trials.registry import registered

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        rows = registered(conn, family)
    table = Table("trial_id", "id", "status", "config_hash")
    for trial_id, name, status, h in rows:
        table.add_row(str(trial_id), name, status, h[:12])
    console.print(table)
    console.print(f"N = {len(rows)}")


@app.command("check")
def check_cmd(
    family: Family,
    config_hash: Annotated[str, typer.Option("--config-hash", help="Full sha256 of the trial config.")],
) -> None:
    """Exit 1 unless the config hash is registered (the guard `train` runs before any fit)."""
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.trials.registry import UnregisteredTrialError, assert_registered

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        try:
            trial_id = assert_registered(conn, family, config_hash)
        except UnregisteredTrialError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"registered: trial_id {trial_id}")
