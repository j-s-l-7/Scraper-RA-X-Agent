"""Command line: ``ideation <command>`` (01 §8.1). Phase 0 commands: bootstrap-role, init-db, doctor, runs."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ideation.config import Settings, get_settings, redact_dsn
from ideation.logs import setup_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Indian-equity investment-ideation system.")
runs_app = typer.Typer(no_args_is_help=True, help="Run registry inspection.")
app.add_typer(runs_app, name="runs")

console = Console()
log = logging.getLogger("ideation.cli")


def _init() -> Settings:
    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    return settings


@app.command("bootstrap-role")
def bootstrap_role_cmd(
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """ONE-TIME, as a superuser: create role ideation_rw + grants + schema ideation (03 §C1). Idempotent."""
    from ideation.db.bootstrap import ROLE_SQL, bootstrap_role

    settings = _init()
    target = redact_dsn(settings.bootstrap_dsn)
    if not yes and not typer.confirm(f"Run {ROLE_SQL.name} as superuser on {target}?"):
        raise typer.Exit(1)
    before, after = bootstrap_role(settings.bootstrap_dsn, connect_timeout_s=settings.connect_timeout_s)
    log.info("bootstrap-role", extra={"before": before.__dict__, "after": after.__dict__, "dsn": target})
    table = Table("attribute", "before", "after")
    for key in after.__dict__:
        table.add_row(key, str(getattr(before, key)), str(getattr(after, key)))
    console.print(table)
    if not after.ok:
        console.print("[red]role state is not as required by 03 §C1[/red]")
        raise typer.Exit(1)
    console.print(
        "[green]ideation_rw ready[/green] (role existed before)"
        if before.exists
        else "[green]ideation_rw created[/green]"
    )


@app.command("init-db")
def init_db_cmd(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List pending migrations; apply nothing.")] = False,
) -> None:
    """Apply sql/ideation/*.sql in order (idempotent), recorded in ideation.schema_migration."""
    from ideation.db.migrate import discover, migrate
    from ideation.db.writer import Writer

    settings = _init()
    migrations = discover()
    with Writer.from_settings(settings).connect() as conn:
        result = migrate(conn, migrations, dry_run=dry_run)
    log.info("init-db", extra={"applied": result.applied, "already": result.already, "pending": result.pending})
    if dry_run:
        console.print(f"pending: {list(result.pending) or 'none'}; already applied: {list(result.already)}")
    else:
        console.print(f"applied: {list(result.applied) or 'none'}; already applied: {list(result.already)}")


@app.command()
def doctor() -> None:
    """Check environment, lake access (as ideation_rw, read-only, RLS bypass), schema version and freshness."""
    from ideation.doctor import Status, run_checks

    settings = _init()
    checks = run_checks(settings, today=date.today())
    table = Table("check", "status", "detail")
    colour = {Status.OK: "green", Status.WARN: "yellow", Status.FAIL: "red"}
    for c in checks:
        table.add_row(c.name, f"[{colour[c.status]}]{c.status}[/]", c.detail)
    console.print(table)
    log.info("doctor", extra={"checks": [c.__dict__ for c in checks]})
    failed = [c.name for c in checks if c.status is Status.FAIL]
    if failed:
        console.print(f"[red]doctor FAILED:[/red] {', '.join(failed)}")
        raise typer.Exit(1)
    console.print("[green]doctor passed[/green]")


@runs_app.command("list")
def runs_list(
    limit: Annotated[int, typer.Option(help="Rows to show.")] = 20,
    kind: Annotated[str | None, typer.Option(help="Filter by kind.")] = None,
) -> None:
    """Most recent runs."""
    from ideation.db.writer import Writer
    from ideation.runs import RunRegistry

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        records = RunRegistry(conn).recent(limit, kind)
    table = Table("run_id", "kind", "as_of", "status", "started", "config", "git")
    for r in records:
        table.add_row(
            str(r.run_id),
            r.kind,
            str(r.as_of or ""),
            r.status,
            f"{r.started_at:%Y-%m-%d %H:%M}",
            (r.config_hash or "")[:12],
            (r.git_sha or "")[:12],
        )
    console.print(table)


@runs_app.command("show")
def runs_show(run_id: int) -> None:
    """One run, all columns."""
    from ideation.db.writer import Writer
    from ideation.runs import RunRegistry

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        record = RunRegistry(conn).get(run_id)
    if record is None:
        console.print(f"run {run_id} not found")
        raise typer.Exit(1)
    console.print(record)
