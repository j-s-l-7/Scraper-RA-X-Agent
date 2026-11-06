"""``ideation features build --anchors june,march [--force]`` (05 §10.3 P2).

Reads the latest snapshot through ``PitStore`` and writes ``IDEATION_DATA_DIR/outputs/<snap_id>/
features_fundamental.parquet`` plus a ``run`` row and the per-accessor ``run_manifest`` records. It never writes the
universe or label tables; the ``feature_complete`` column feeds the next ``ideation universe build --force``.
"""

from __future__ import annotations

import sys
import time
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ideation.config import get_settings, load_methodology
from ideation.pit.calendar import parse_anchors
from ideation.pit.contract import PitStore, records_to_rows

app = typer.Typer(no_args_is_help=True, help="PIT feature builders (fundamental).")
console = Console(width=None if sys.stdout.isatty() else 200)


@app.command("build")
def build_cmd(
    anchors: Annotated[str, typer.Option(help="june,march,monthly and/or YYYY-MM-DD dates.")] = "june,march",
    force: Annotated[bool, typer.Option("--force", help="Rebuild even if an identical run succeeded.")] = False,
) -> None:
    """Build fundamental features (and feature_complete / c_i) for the anchors."""
    from ideation.db.writer import Writer
    from ideation.features.build import build_all, coverage_path, features_path
    from ideation.features.registry import load_registry
    from ideation.logs import setup_logging
    from ideation.runs import RunRegistry
    from ideation.universe.build import UniverseConfig

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    store = PitStore.latest(settings.data_dir)
    ucfg = UniverseConfig.from_methodology(meth.data)
    anchor_list = parse_anchors(store.calendar, anchors)
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        reg_runs = RunRegistry(conn)
        params = {"anchors": anchors, "n_anchors": len(anchor_list), "derived": store.derived_sha(),
                  "features": list(reg.files)}  # fmt: skip
        with reg_runs.track("features", config_hash=meth.config_hash, snap_id=store.snap_id, params=params,
                            force=force) as run:  # fmt: skip
            if run.skipped:
                console.print(f"skipped: identical run {run.record.stats} already ok (use --force)")
                return
            feats, records, cov = build_all(store, reg, ucfg, anchor_list)
            out = features_path(settings.data_dir, store.snap_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            feats.to_parquet(out, index=False)
            cov.to_parquet(coverage_path(settings.data_dir, store.snap_id), index=False)
            conn.execute("delete from ideation.run_manifest where run_id = %s", (run.run_id,))
            conn.copy_rows(
                "ideation.run_manifest",
                ["run_id", "anchor", "item", "n_rows", "sha256", "max_available_from"],
                ((run.run_id, *r) for r in records_to_rows(records)),
            )
            run.stats["coverage"] = [
                {k: (None if pd.isna(v) else (str(v) if k == "anchor" else v)) for k, v in r.items()}
                for r in cov.to_dict("records")
            ]
            run.stats.update({"anchors": len(anchor_list), "rows": len(feats),
                              "seconds": round(time.perf_counter() - t0, 1)})  # fmt: skip
    cols = ["anchor", "U_score before", "after hook", "dropped", "fin", "mean fc", "roe", "ep", "ebitda_ev",
            "fcf_yield", "roic", "rev_g1", "roe_3y", "eps_g3", "nd_ebitda"]  # fmt: skip
    t = Table(*cols, title=f"U_score with the hook ({store.snap_id})")

    def pct(v: float | None) -> str:
        return "n/a" if v is None or pd.isna(v) else f"{v * 100:.0f}%"

    for r in cov[cov["anchor"].map(lambda d: d.month in (3, 6))].itertuples():
        fc = "n/a" if pd.isna(r.feature_complete_mean) else f"{r.feature_complete_mean:.3f}"
        t.add_row(str(r.anchor), str(r.u_score_before), str(r.u_score_after), str(r.dropped), str(r.n_financial), fc,
                  *(pct(getattr(r, n)) for n in ("roe", "ep", "ebitda_ev", "fcf_yield", "roic", "rev_g1", "roe_3y",
                                                 "eps_g3", "nd_ebitda")))  # fmt: skip
    console.print(t)
    console.print(f"[green]features built[/green]: {len(anchor_list)} anchors, {len(feats)} rows,"
                  f" {time.perf_counter() - t0:.1f} s -> {out}")  # fmt: skip
