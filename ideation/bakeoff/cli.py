"""``ideation train --horizon 1y --bakeoff --cpcv`` and ``ideation backtest report`` (05 §10.3 MS1)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

console = Console(width=None if sys.stdout.isatty() else 200)
backtest_app = typer.Typer(no_args_is_help=True, help="Backtest tables, gates and persistence from stored results.")
THREADS = "4"


def _limit_threads() -> None:
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[k] = THREADS


def latest_snap(data_dir: Path) -> str:
    cands = sorted(p.name for p in (data_dir / "outputs").iterdir() if (p / "features_fundamental.parquet").exists())
    if not cands:
        raise FileNotFoundError("no outputs/<snap> directory with features_fundamental.parquet")
    return cands[-1]


def train_cmd(
    horizon: Annotated[str, typer.Option(help="1y (this milestone) | tactical is MS2.")] = "1y",
    bakeoff: Annotated[bool, typer.Option("--bakeoff", help="Run every runnable trial of the family.")] = False,
    cpcv: Annotated[bool, typer.Option("--cpcv", help="Also run the 36-split CPCV for every trial.")] = False,
    family: Annotated[str, typer.Option(help="Pre-registered family.")] = "1Y-june",
    trials: Annotated[str, typer.Option(help="Comma-separated trial ids to run (default: all runnable).")] = "",
    snap: Annotated[str, typer.Option(help="Snapshot id (default: latest with features).")] = "",
    report: Annotated[
        bool, typer.Option("--report/--no-report", help="Compute tables/gates and persist after.")
    ] = True,
    text: Annotated[
        bool, typer.Option("--text", help="MS1-text: run the registered text trials (#44-45) + control arms only.")
    ] = False,
) -> None:
    """Fit the pre-registered 1Y trials (walk-forward 2019-2024) [+ CPCV]; refuses an unregistered config hash."""
    _limit_threads()
    if horizon.lower() != "1y" or not bakeoff:
        console.print("[red]only `--horizon 1y --bakeoff` is implemented here (tactical is MS2)[/red]")
        raise typer.Exit(2)
    from threadpoolctl import threadpool_limits

    from ideation.bakeoff.driver import load_panels, run_bakeoff
    from ideation.cli import _init
    from ideation.config import load_methodology
    from ideation.db.writer import Writer
    from ideation.features.registry import load_registry
    from ideation.runs import RunRegistry
    from ideation.trials.registry import assert_registered, load_family

    settings = _init()
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    specs = load_family(family)
    snap_id = snap or latest_snap(settings.data_dir)
    only = {t.strip() for t in trials.split(",") if t.strip()} or None
    if text:
        only = {s.trial_id for s in specs if s.config.get("subset")}
        report = False
    with Writer.from_settings(settings).connect() as conn:
        ids = {s.trial_id: assert_registered(conn, family, s.config_hash) for s in specs}
        rr = RunRegistry(conn)
        params = {"family": family, "cpcv": cpcv, "snap": snap_id, "trials": sorted(only) if only else "all"}
        run = rr.start("train", config_hash=meth.config_hash, snap_id=snap_id, params=params, force=True)
    run_id = run.run_id
    console.print(f"run {run_id}: family {family}, snapshot {snap_id}, cpcv={cpcv}")
    panels = load_panels(settings.data_dir, snap_id, {s.config["anchor_family"] for s in specs}, reg, with_text=text)
    t0 = time.perf_counter()
    seconds: dict[str, float] = {}

    def on_done(o) -> None:
        seconds[o.spec.trial_id] = round(o.seconds, 1)
        if o.spec.trial_id not in ids:  # a protocol control arm is not a trial
            return
        with Writer.from_settings(settings).connect() as c:
            c.execute(
                "update ideation.experiment_trial set status = %s, run_id = %s where trial_id = %s",
                (o.status, run_id, ids[o.spec.trial_id]),
            )

    status = "ok"
    try:
        with threadpool_limits(int(THREADS)):
            outcomes = run_bakeoff(
                specs,
                panels,
                reg,
                settings.data_dir,
                cpcv=cpcv,
                only=only,
                log_fn=lambda m: console.print(f"[{time.strftime('%H:%M:%S')}] {m}", highlight=False),
                on_done=on_done,
            )
    except BaseException:
        status = "failed"
        raise
    finally:
        with Writer.from_settings(settings).connect() as conn:
            RunRegistry(conn).finish(
                run_id, status, stats={"seconds": round(time.perf_counter() - t0, 1), "trial_seconds": seconds}
            )
    failed = [o.spec.trial_id for o in outcomes if o.status == "failed"]
    console.print(f"[green]bake-off done[/green] in {time.perf_counter() - t0:.0f} s; failed: {failed or 'none'}")
    if report:
        from ideation.bakeoff.finalize import finalize

        finalize(settings, family, snap_id, run_id, specs, panels, reg, ids)


@backtest_app.command("report")
def report_cmd(
    family: Annotated[str, typer.Option()] = "1Y-june",
    snap: Annotated[str, typer.Option()] = "",
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
    leakage_green: Annotated[
        bool | None, typer.Option("--leakage-green/--leakage-red", help="Result of the leakage suites (gate 5).")
    ] = None,
) -> None:
    """Tables R-A..R-D, DSR/PBO/90-10, gates and persistence from stored results (no refits)."""
    _limit_threads()
    from ideation.bakeoff.driver import load_panels
    from ideation.bakeoff.finalize import finalize
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.features.registry import load_registry
    from ideation.trials.registry import assert_registered, load_family

    settings = _init()
    reg = load_registry(settings.config_dir)
    specs = load_family(family)
    snap_id = snap or latest_snap(settings.data_dir)
    with Writer.from_settings(settings).connect() as conn:
        ids = {s.trial_id: assert_registered(conn, family, s.config_hash) for s in specs}
    panels = load_panels(settings.data_dir, snap_id, {s.config["anchor_family"] for s in specs}, reg)
    finalize(settings, family, snap_id, None, specs, panels, reg, ids, persist=persist, leakage_green=leakage_green)


@backtest_app.command("text")
def text_cmd(
    family: Annotated[str, typer.Option()] = "1Y-june",
    snap: Annotated[str, typer.Option()] = "",
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
) -> None:
    """MS1-text: paired statistics, report and persistence of trials #44-45 (no refits)."""
    _limit_threads()
    from ideation.bakeoff.driver import load_panels
    from ideation.bakeoff.text import text_report
    from ideation.cli import _init
    from ideation.db.writer import Writer
    from ideation.features.registry import load_registry
    from ideation.trials.registry import assert_registered, load_family

    settings = _init()
    reg = load_registry(settings.config_dir)
    specs = load_family(family)
    snap_id = snap or latest_snap(settings.data_dir)
    tspecs = [s for s in specs if s.config.get("subset")]
    with Writer.from_settings(settings).connect() as conn:
        ids = {s.trial_id: assert_registered(conn, family, s.config_hash) for s in tspecs}
    panels = load_panels(settings.data_dir, snap_id, {s.config["anchor_family"] for s in tspecs}, reg, with_text=True)
    text_report(settings, family, snap_id, None, specs, panels, ids, persist=persist)
