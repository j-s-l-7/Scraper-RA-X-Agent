"""``ideation features-tech`` (05 §10.3 P3): the tactical feature matrix, the regime history and the RRG table.

* ``build --anchors monthly,june,march [--workers N]``: technical + flow + event + regime columns for every anchor ->
  ``IDEATION_DATA_DIR/outputs/<snap>/features_tech.parquet`` (+ ``regime_history.parquet``), a ``run`` row (kind
  ``features_tech``) and ``run_manifest`` records.
* ``regime --as-of latest|YYYY-MM-DD [--no-write]``: prints the regime vector, labels and RRG tables, and upserts
  ``ideation.regime_snapshot`` / ``ideation.sector_rotation``.
* ``history --from 2020-01 --to 2020-06``: prints the stored regime history.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from datetime import date
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ideation.config import get_settings, load_methodology
from ideation.features.registry import Registry, load_registry
from ideation.features.tech_build import AnchorResult, build_anchor
from ideation.pit.calendar import parse_anchors
from ideation.pit.contract import PitStore, records_to_rows
from ideation.universe.build import UniverseConfig

app = typer.Typer(no_args_is_help=True, help="Tactical technical / flow / event / regime features and RRG (P3).")
console = Console(width=None if sys.stdout.isatty() else 200)

FEATURES_FILE = "features_tech.parquet"
REGIME_FILE = "regime_history.parquet"
_G: dict[str, Any] = {}


def _work(anchor: date) -> tuple[date, AnchorResult]:
    return anchor, build_anchor(_G["store"], _G["reg"], _G["ucfg"], anchor, rebuild_universe=_G["rebuild"],
                                with_rrg_table=False)  # fmt: skip


def build_all(store: PitStore, reg: Registry, ucfg: UniverseConfig | None, anchors: list[date], *,
              workers: int = 1, rebuild_universe: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, list]:  # fmt: skip
    """(features, regime history, manifest records) for ``anchors``; rows sorted by (anchor, security_id)."""
    _G.update(store=store, reg=reg, ucfg=ucfg, rebuild=rebuild_universe)
    if workers > 1:
        with mp.get_context("fork").Pool(workers) as pool:
            results = pool.map(_work, anchors, chunksize=1)
    else:
        results = [_work(a) for a in anchors]
    results.sort(key=lambda t: t[0])
    feats = pd.concat([r.features for _, r in results], ignore_index=True)
    hist = pd.DataFrame(
        [{"anchor": a, **r.regime, **{f"label_{k}": v for k, v in r.labels.items()}} for a, r in results]
    )
    records = [rec for _, r in results for rec in r.records]
    return feats, hist, records


def _print_regime(vec: dict[str, float], labels: dict[str, str]) -> None:
    t = Table("metric", "value", title="Regime vector")
    for k, v in vec.items():
        t.add_row(k, "n/a" if pd.isna(v) else f"{v:.4g}")
    console.print(t)
    console.print("labels: " + ", ".join(f"{k}={v or '-'}" for k, v in labels.items()))


def _print_rrg(rrg: pd.DataFrame) -> None:
    for kind in ("industry_group", "sector", "index"):
        sub = rrg[rrg["group_type"] == kind].sort_values("rs_ratio", ascending=False)
        if sub.empty:
            continue
        t = Table("code", "RS-Ratio", "RS-Mom", "quadrant", "members", title=f"RRG {kind} ({rrg['anchor'].iloc[0]})")
        for r in sub.itertuples():
            t.add_row(str(r.group_code), "n/a" if pd.isna(r.rs_ratio) else f"{r.rs_ratio:.1f}",
                      "n/a" if pd.isna(r.rs_mom) else f"{r.rs_mom:.1f}", str(r.quadrant),
                      "" if pd.isna(r.n_members) else str(int(r.n_members)))  # fmt: skip
        console.print(t)


@app.command("build")
def build_cmd(
    anchors: Annotated[str, typer.Option(help="june,march,monthly and/or YYYY-MM-DD dates.")] = "monthly",
    workers: Annotated[int, typer.Option(help="Processes (fork). 1 = in-process.")] = 6,
    rebuild_universe: Annotated[bool, typer.Option("--rebuild-universe", help="Rebuild membership in memory.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Rebuild even if an identical run succeeded.")] = False,
) -> None:
    """Build the tactical feature matrix for the anchors."""
    from ideation.db.writer import Writer
    from ideation.logs import setup_logging
    from ideation.runs import RunRegistry

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    snap = PitStore.latest(settings.data_dir)
    store = PitStore(snap.snapshot_dir, outputs_dir=settings.data_dir / "outputs" / snap.snap_id)
    ucfg = UniverseConfig.from_methodology(meth.data)
    anchor_list = parse_anchors(store.calendar, anchors)
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        params = {"anchors": anchors, "n_anchors": len(anchor_list), "derived": store.derived_sha(),
                  "features": list(reg.files), "rebuild_universe": rebuild_universe}  # fmt: skip
        with RunRegistry(conn).track("features_tech", config_hash=meth.config_hash, snap_id=store.snap_id,
                                     params=params, force=force) as run:  # fmt: skip
            if run.skipped:
                console.print(f"skipped: identical run {run.record.stats} already ok (use --force)")
                return
            feats, hist, records = build_all(store, reg, ucfg, anchor_list, workers=workers,
                                             rebuild_universe=rebuild_universe)  # fmt: skip
            out = settings.data_dir / "outputs" / store.snap_id
            out.mkdir(parents=True, exist_ok=True)
            feats.to_parquet(out / FEATURES_FILE, index=False)
            hist.to_parquet(out / REGIME_FILE, index=False)
            conn.execute("delete from ideation.run_manifest where run_id = %s", (run.run_id,))
            conn.copy_rows("ideation.run_manifest",
                           ["run_id", "anchor", "item", "n_rows", "sha256", "max_available_from"],
                           ((run.run_id, *r) for r in records_to_rows(records)))  # fmt: skip
            secs = round(time.perf_counter() - t0, 1)
            run.stats.update({"anchors": len(anchor_list), "rows": len(feats), "seconds": secs, "workers": workers})
    console.print(f"[green]features-tech built[/green]: {len(anchor_list)} anchors, {len(feats)} rows,"
                  f" {len(feats.columns)} columns, {secs} s -> {out / FEATURES_FILE}")  # fmt: skip
    cols = ["anchor", "pct_above_200dma", "pct_above_50dma", "div_6m", "vix", "vix_pct_3y", "n500_ma200_gap",
            "label_trend", "label_vol", "label_breadth"]  # fmt: skip
    cols = [c if c in hist.columns else f"regime_{c}" for c in cols]
    console.print(hist.tail(12)[[c for c in cols if c in hist.columns]].to_string(index=False))


@app.command("regime")
def regime_cmd(
    as_of: Annotated[str, typer.Option(help="latest or YYYY-MM-DD (snapped to a good session).")] = "latest",
    write: Annotated[bool, typer.Option("--write/--no-write", help="Upsert regime_snapshot / sector_rotation.")] = True,
) -> None:
    """Regime vector, labels and the RRG tables at one anchor."""
    from ideation.db.writer import Writer
    from ideation.logs import setup_logging
    from ideation.runs import RunRegistry

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    snap = PitStore.latest(settings.data_dir)
    store = PitStore(snap.snapshot_dir, outputs_dir=settings.data_dir / "outputs" / snap.snap_id)
    ucfg = UniverseConfig.from_methodology(meth.data)
    if as_of == "latest":
        anchor = store.calendar.good[-1].astype(object)
    else:
        anchor = store.calendar.snap_anchor(date.fromisoformat(as_of))
    res = build_anchor(store, reg, ucfg, anchor)
    console.print(f"[bold]as of {anchor}[/bold] (snapshot {store.snap_id})")
    _print_regime(res.regime, res.labels)
    _print_rrg(res.rrg)
    if not write:
        return
    with (
        Writer.from_settings(settings).connect() as conn,
        RunRegistry(conn).track("regime", config_hash=meth.config_hash, snap_id=store.snap_id,
                                params={"as_of": str(anchor)}, force=True) as run,
    ):  # fmt: skip
            metrics = {k: (None if pd.isna(v) else float(v)) for k, v in res.regime.items()}
            conn.execute(
                "insert into ideation.regime_snapshot (as_of, run_id, metrics, labels) values (%s, %s, %s, %s) "
                "on conflict (as_of) do update set run_id = excluded.run_id, metrics = excluded.metrics, "
                "labels = excluded.labels",
                (anchor, run.run_id, json.dumps(metrics), json.dumps(res.labels)),
            )
            conn.execute("delete from ideation.sector_rotation where as_of = %s", (anchor,))
            conn.copy_rows(
                "ideation.sector_rotation",
                ["as_of", "group_type", "group_code", "rs_ratio", "rs_mom", "quadrant", "n_members", "run_id"],
                ((anchor, r.group_type, r.group_code, None if pd.isna(r.rs_ratio) else float(r.rs_ratio),
                  None if pd.isna(r.rs_mom) else float(r.rs_mom), r.quadrant,
                  None if pd.isna(r.n_members) else int(r.n_members), run.run_id) for r in res.rrg.itertuples()),
            )  # fmt: skip
            run.stats.update({"as_of": str(anchor), "rrg_rows": len(res.rrg)})
    console.print("[green]regime_snapshot and sector_rotation written[/green]")


@app.command("history")
def history_cmd(
    start: Annotated[str, typer.Option("--from", help="YYYY-MM")] = "2019-12",
    end: Annotated[str, typer.Option("--to", help="YYYY-MM")] = "2020-12",
) -> None:
    """Print the stored monthly regime history (written by ``build``)."""
    settings = get_settings()
    snap = PitStore.latest(settings.data_dir)
    path = settings.data_dir / "outputs" / snap.snap_id / REGIME_FILE
    if not path.is_file():
        raise typer.Exit(f"no {REGIME_FILE}; run `ideation features-tech build` first")
    h = pd.read_parquet(path)
    key = h["anchor"].map(lambda d: d.strftime("%Y-%m"))
    h = h[(key >= start) & (key <= end)]
    show = ["anchor", "pct_above_200dma", "pct_above_50dma", "div_6m", "vix", "vix_pct_3y", "n500_ma200_gap",
            "n500_dd_252", "label_trend", "label_vol", "label_breadth", "label_narrow_rally",
            "label_macro"]  # fmt: skip
    console.print(h[[c for c in show if c in h.columns]].round(2).to_string(index=False))
