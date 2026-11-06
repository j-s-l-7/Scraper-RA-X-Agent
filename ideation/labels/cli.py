"""``ideation labels build --horizon 1Y,3M,1M --anchors june,monthly`` (02 §3, 03 §C3, 05 §1.3, §8).

Needs the universe outputs of the same snapshot (``ideation universe build``). It writes
``IDEATION_DATA_DIR/outputs/<snap_id>/labels.parquet`` and ``ideation.label``.

Labels for sealed anchors are stored too. The QA summary reads them back through ``load_labels(purpose='qa')``
and only for unsealed anchors (05 §8).
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ideation.config import get_settings, load_methodology
from ideation.labels.build import compute_labels, label_specs
from ideation.labels.loader import is_sealed, load_labels, unsealed_only
from ideation.pit.calendar import parse_anchors
from ideation.pit.contract import PitStore

app = typer.Typer(no_args_is_help=True, help="Forward-return labels with exits and holdout sealing.")
console = Console(width=None if sys.stdout.isatty() else 200)
log = logging.getLogger("ideation.labels")

LABEL_COLS = (
    "anchor", "security_id", "horizon", "entry_date", "label_end", "label_available_from", "ret_total", "ret_price",
    "excess_univ_med", "excess_univ_cw", "excess_n500_pr", "xs_rank", "exit_type", "stale_days", "flags",
    "entry_price", "exit_price", "last_trade", "ret_total_last_price",
)  # fmt: skip


def _py(v: Any) -> Any:
    if isinstance(v, np.ndarray):
        return [str(x) for x in v]
    if isinstance(v, list):
        return [str(x) for x in v]
    if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def labels_path(data_dir: Path, snap_id: str) -> Path:
    return data_dir / "outputs" / snap_id / "labels.parquet"


def write_db(conn: Any, run_id: int, labels: pd.DataFrame) -> None:
    with conn.transaction():
        for h, g in labels.groupby("horizon"):
            conn.execute(
                "delete from ideation.label where horizon = %s and anchor = any(%s)", (h, sorted(set(g["anchor"])))
            )
        rows = ((*(_py(v) for v in r), run_id) for r in labels[list(LABEL_COLS)].itertuples(index=False))
        conn.copy_rows("ideation.label", [*LABEL_COLS, "run_id"], rows)


def exit_summary(labels: pd.DataFrame) -> pd.DataFrame:
    """Exit types per horizon over unsealed anchors (sealed rows are not read)."""
    keep = [not is_sealed(a, h) for a, h in zip(labels["anchor"], labels["horizon"], strict=True)]
    x = labels[keep]
    return x.groupby(["horizon", "exit_type"]).size().unstack(fill_value=0)


def bias_inputs(store: PitStore, universe: pd.DataFrame, path: Path) -> dict[str, Any]:
    """Numbers the bias block prints (02 §8.2), over UNSEALED June 1Y labels read through the loader (qa)."""
    from ideation.labels.exits_report import exit_counts_02, stitched_exit_summary

    june = unsealed_only(sorted({a for a in universe["anchor"] if a.month == 6}), "1Y")
    q = load_labels(june, "1Y", "qa", source=path, unsealed=set())
    u = universe.set_index(["anchor", "security_id"])
    key = pd.MultiIndex.from_arrays([q["anchor"], q["security_id"]])
    q = q.assign(covered=u["company_id"].reindex(key).notna().to_numpy(),
                 in_u_score=u["in_u_score"].reindex(key).fillna(False).to_numpy())  # fmt: skip
    gap = {
        str(a): round(float(g.loc[g["covered"], "ret_total"].median() - g.loc[~g["covered"], "ret_total"].median()), 4)
        for a, g in q.groupby("anchor") if (~g["covered"]).any()
    }  # fmt: skip
    flags = q["flags"].map(lambda v: set(v))
    return {
        "june_1y_anchors": [str(a) for a in june],
        "covered_minus_uncovered_median_1y": gap,
        "exit_types_1y": stitched_exit_summary(q),
        "distress_1y": int((q["exit_type"] == "distress").sum()),
        "corp_exit_1y": int((q["exit_type"] == "corp_exit").sum()),
        "stale_1y": int((q["exit_type"] == "stale").sum()),
        "rights_unadjusted_1y": int(flags.map(lambda f: "rights_unadjusted" in f).sum()),
        "no_div_data_share_1y": round(float(flags.map(lambda f: "no_div_data" in f).mean()), 4),
        "u_score_no_div_data_share_1y": round(
            float(flags[q["in_u_score"]].map(lambda f: "no_div_data" in f).mean()), 4
        ),
        "univ_median_1y_minus100": {str(a): round(float(g["ret_total"].median()), 4) for a, g in q.groupby("anchor")},
        "univ_median_1y_lastprice": {
            str(a): round(float(g["ret_total_last_price"].median()), 4) for a, g in q.groupby("anchor")
        },
        "jump_factors_applied": int((store.table("factors")["kind"] == "jump").sum()),
        "exits_02": exit_counts_02(store),
    }


@app.command("build")
def build_cmd(
    horizon: Annotated[str, typer.Option(help="Comma-separated: 1Y,3M,1M.")] = "1Y,3M,1M",
    anchors: Annotated[str, typer.Option(help="june,march,monthly and/or dates.")] = "june,monthly",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Build labels for the anchors' universes and store them."""
    from ideation.db.writer import Writer
    from ideation.logs import setup_logging
    from ideation.runs import RunRegistry

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    out_dir = settings.data_dir / "outputs"
    store = PitStore.latest(settings.data_dir)
    upath = out_dir / store.snap_id / "universe_member.parquet"
    if not upath.is_file():
        raise typer.BadParameter(f"no universe for snapshot {store.snap_id}; run `ideation universe build` first")
    universe = pd.read_parquet(upath)
    horizons = [h.strip() for h in horizon.split(",") if h.strip()]
    anchor_list = [a for a in parse_anchors(store.calendar, anchors) if a in set(universe["anchor"])]
    specs = label_specs(store.calendar, anchor_list, horizons)
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        reg = RunRegistry(conn)
        params = {"horizons": horizons, "anchors": anchors, "derived": store.derived_sha()}
        with reg.track("labels", config_hash=meth.config_hash, snap_id=store.snap_id, params=params,
                       force=force) as run:  # fmt: skip
            if run.skipped:
                console.print("skipped: identical labels run already ok (use --force)")
                return
            labels = compute_labels(store, universe, specs)
            path = labels_path(settings.data_dir, store.snap_id)
            if path.is_file():  # keep other horizons / anchors already built for this snapshot
                old = pd.read_parquet(path)
                done = set(zip(labels["anchor"], labels["horizon"], strict=True))
                old = old[[(a, h) not in done for a, h in zip(old["anchor"], old["horizon"], strict=True)]]
                labels_all = pd.concat([old, labels], ignore_index=True)
            else:
                labels_all = labels
            labels_all.to_parquet(path, index=False)
            write_db(conn, run.run_id, labels)
            run.stats.update({"specs": len(specs), "rows": len(labels), "seconds": round(time.perf_counter() - t0, 1)})
            run.stats["bias_inputs"] = bias_inputs(store, universe, path)
    ex = exit_summary(labels)
    t = Table("horizon", *map(str, ex.columns), title="exit types (unsealed anchors)")
    for h, row in ex.iterrows():
        t.add_row(str(h), *map(str, row.tolist()))
    console.print(t)
    june = unsealed_only([a for a in anchor_list if a.month == 6], "1Y")
    if "1Y" in horizons and june:
        q = load_labels(june, "1Y", "qa", source=labels_path(settings.data_dir, store.snap_id), unsealed=set())
        console.print(f"1Y June labels (unsealed {june[0]}..{june[-1]}): {len(q)} rows; "
                      f"median ret {q.groupby('anchor')['ret_total'].median().round(3).to_dict()}")  # fmt: skip
    sealed = sorted({str(a) for a, h in zip(labels["anchor"], labels["horizon"], strict=True) if is_sealed(a, h)})
    console.print(f"sealed anchors stored but not read: {len(sealed)} ({sealed[:2]}…)")
    console.print(f"[green]labels built[/green]: {len(specs)} anchor×horizon, {len(labels)} rows, "
                  f"{time.perf_counter() - t0:.1f} s")  # fmt: skip
