"""Command line: ``ideation <command>`` (01 §8.1). Phase 0 commands: bootstrap-role, init-db, doctor, runs."""

from __future__ import annotations

import logging
import sys
from datetime import date
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ideation.config import Settings, get_settings, load_methodology, redact_dsn
from ideation.logs import setup_logging
from ideation.trials.cli import app as trials_app

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Indian-equity investment-ideation system.")
runs_app = typer.Typer(no_args_is_help=True, help="Run registry inspection.")
app.add_typer(runs_app, name="runs")
app.add_typer(trials_app, name="trials")


def _register_subapps() -> None:
    from ideation.bakeoff.cli import backtest_app, train_cmd
    from ideation.features.cli import app as features_app
    from ideation.features.tech_cli import app as features_tech_app
    from ideation.labels.cli import app as labels_app
    from ideation.outcomes.cli import app as outcomes_app
    from ideation.report.cli import holdout_app, report_app
    from ideation.tactical.cli import app as tactical_app
    from ideation.text.cli import app as text_app
    from ideation.universe.cli import app as universe_app

    app.add_typer(universe_app, name="universe")
    app.add_typer(labels_app, name="labels")
    app.add_typer(text_app, name="text")
    app.add_typer(features_app, name="features")
    app.add_typer(features_tech_app, name="features-tech")
    app.add_typer(tactical_app, name="tactical")
    app.add_typer(backtest_app, name="backtest")
    app.command("train")(train_cmd)
    app.add_typer(outcomes_app, name="outcomes")
    app.add_typer(report_app, name="report")
    app.add_typer(holdout_app, name="holdout")


_register_subapps()

console = Console(width=None if sys.stdout.isatty() else 200)
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
def snapshot(
    full: Annotated[
        bool, typer.Option("--full/--delta", help="Full pull, or incremental bhav/yf over the last.")
    ] = True,
    force: Annotated[bool, typer.Option("--force", help="Re-run even if the lake state is unchanged.")] = False,
) -> None:
    """Snapshot the lake to parquet (one REPEATABLE READ READ ONLY transaction) + derived calendar/stitching."""
    import time

    from ideation.db.writer import Writer
    from ideation.lake.adapter import LakeMode, LakeReader
    from ideation.lake.snapshot import build_derived, fetch_lake_state, register_snapshot, take_snapshot
    from ideation.runs import RunRegistry, git_sha

    settings = _init()
    meth = load_methodology()
    reader = LakeReader.from_settings(settings, LakeMode.SNAPSHOT)
    reader.statement_timeout_s = max(reader.statement_timeout_s, 900)
    state = fetch_lake_state(reader)
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        reg = RunRegistry(conn)
        params = {"mode": "full" if full else "delta", "lake_state": {k: str(v) for k, v in state.items()}}
        with reg.track("snapshot", config_hash=meth.config_hash, as_of=state["max_bhav"], params=params,
                       force=force) as run:  # fmt: skip
            if run.skipped:
                console.print(f"skipped: lake unchanged since run {run.record.stats} (use --force)")
                return
            sha = git_sha()
            res = take_snapshot(reader, settings.data_dir, git_sha=sha, config_hash=meth.config_hash, delta=not full)
            t_pull = time.perf_counter() - t0
            derived = build_derived(res.path, git_sha=sha)
            register_snapshot(conn, run.run_id, res, derived)
            run.stats.update(
                {
                    "snap_id": res.snap_id, "reused": res.reused, "pull_finalise_s": round(t_pull, 1),
                    "derived_s": derived["build_s"], "total_s": round(time.perf_counter() - t0, 1),
                }
            )  # fmt: skip
    st = derived["stats"]
    table = Table("dataset", "rows", "max available_from")
    for name, d in res.manifest["datasets"].items():
        table.add_row(name, f"{d['rows']:,}", d["max_available_from"])
    console.print(table)
    console.print(
        f"[green]snapshot {res.snap_id}[/green] ({'reused' if res.reused else 'new'}) in {res.path}; "
        f"securities {st['stitch']['securities']}, with company {st['stitch']['securities_with_company']}; "
        f"bhav-missing sessions {len(st['calendar']['bhav_missing'])}; {time.perf_counter() - t0:.1f} s"
    )


@app.command()
def coverage(
    anchors: Annotated[str, typer.Option(help="june (default), march, monthly or dates.")] = "june",
) -> None:
    """Per-anchor universe coverage (U_score / ₹0.5 cr / U_score_T / U1000) and the 02 §0.1 reproduction."""
    from ideation.db.writer import Writer
    from ideation.pit.calendar import parse_anchors
    from ideation.pit.contract import PitStore

    settings = _init()
    store = PitStore.latest(settings.data_dir)
    want = parse_anchors(store.calendar, anchors)
    with Writer.from_settings(settings).connect() as conn:
        rows = conn.execute(
            "select anchor, n_traded, n_u1000, n_u_score, n_u_score_adv05, n_u_score_t, n_no_company, n_thin_trading,"
            " n_adv_below_floor, n_too_new, n_mcap_unknown, n_mcap_below_floor_100, scorable_share_top500,"
            " scorable_share_top1000, extra from ideation.universe_coverage where anchor = any(%s) order by anchor",
            (want,),
        ).fetchall()
    table = Table("anchor", "traded", "U1000", "U_score", "≥₹0.5cr", "U_score_T", "no_co", "thin", "adv<0.2",
                  "new", "mcap?", "mcap<100", "U_score∩U500", "U_score∩U1000",
                  title=f"universe coverage (snapshot {store.snap_id})")  # fmt: skip
    repro = []
    for r in rows:
        table.add_row(str(r[0]), *[str(x) for x in r[1:12]], f"{r[12]:.1%}", f"{r[13]:.1%}")
        if r[14] and "coverage_02" in r[14]:
            repro.append(r[14]["coverage_02"])
    console.print(table)
    if repro:
        t2 = Table("anchor", "traded", "top500 fund", "top1000 fund", "all fund", "Δpp vs 02 §0.1 (02 map)",
                   "Δpp (stitched)", title="02 §0.1 reproduction (trailing-90d turnover, 02 definition)")  # fmt: skip
        for c in repro:
            t2.add_row(c["anchor"], str(c["n_traded"]), f"{c['top500_fund_02map']:.1%}",
                       f"{c['top1000_fund_02map']:.1%}", f"{c['all_fund_02map']:.1%}", str(c.get("delta_pp_02map")),
                       str(c.get("delta_pp_stitched")))  # fmt: skip
        console.print(t2)
    if not rows:
        console.print("no coverage rows for these anchors; run `ideation universe build` first")


@app.command("bias-block")
def bias_block_cmd(
    run_id: Annotated[int | None, typer.Option(help="Run to describe (default: latest ok labels run).")] = None,
) -> None:
    """Print the mandatory Data & Bias block (02 §8.2) with this snapshot's measured numbers."""
    from ideation.db.writer import Writer
    from ideation.eval.bias_block import render_bias_block

    settings = _init()
    with Writer.from_settings(settings).connect() as conn:
        if run_id is None:
            row = conn.execute(
                "select max(run_id) from ideation.run where kind = 'labels' and status = 'ok'"
            ).fetchone()
            run_id = row[0] if row else None
        if run_id is None:
            console.print("no ok labels run yet: run `ideation labels build` first")
            raise typer.Exit(1)
        console.print(render_bias_block(run_id, conn=conn), markup=False, highlight=False, soft_wrap=True)


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
