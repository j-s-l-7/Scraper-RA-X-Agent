"""``ideation predict [--horizon both|1y|3m] [--as-of latest]`` (01 §8.1, 05 §10.3 P6).

Live feature build as of the latest snapshot session -> shipped model scores (P1_COMP_EW for 1Y, T_EW_SIGNPRIOR for
3M; no model was promoted, 05 §10.3 / STATUS MS1-MS3) -> R3 percentile / Ideation Score -> R5 calibration and R6
conformal band from the stored counted OOS -> OOD guard -> beta/vol-neutral ranks -> ``ideation.prediction``.
Research challengers (P1_UNIFIED_XS, ENS_RANKAVG_T) are scored beside it and labelled, never used for ranking.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console

from ideation.config import get_settings, load_methodology
from ideation.features.registry import load_registry
from ideation.pit.contract import PitStore
from ideation.universe.build import UniverseConfig

app = typer.Typer(invoke_without_command=True, no_args_is_help=False, help="Live scores, calibration, OOD (P6).")
console = Console(width=None if sys.stdout.isatty() else 200)
SHIPPED = {"1Y": ("1Y-june", "P1_COMP_EW", "P1_UNIFIED_XS"), "3M": ("tactical-3M", "T_EW_SIGNPRIOR", "ENS_RANKAVG_T")}
HORIZONS = {"both": ("1Y", "3M"), "1y": ("1Y",), "3m": ("3M",), "tactical": ("3M",)}


def _model_id(conn: Any, config_hash: str, horizon: str) -> int | None:
    r = conn.execute("select model_id from ideation.model where params->>'config_hash' = %s and horizon = %s"
                     " order by model_id desc limit 1", (config_hash, horizon)).fetchone()  # fmt: skip
    return int(r[0]) if r else None


@app.callback(invoke_without_command=True)
def predict_cmd(
    horizon: Annotated[str, typer.Option(help="both | 1y | 3m (tactical)")] = "both",
    as_of: Annotated[str, typer.Option(help="latest or YYYY-MM-DD (snapped to a good session)")] = "latest",
    ens: Annotated[bool, typer.Option("--ens/--no-ens", help="Refit and score the ENS_RANKAVG_T research challenger (minutes).")] = True,
    force: Annotated[bool, typer.Option("--force", help="Recompute even if an identical run succeeded.")] = False,
) -> None:
    from ideation.db.writer import Writer
    from ideation.logs import setup_logging
    from ideation.predict import cal as calmod
    from ideation.predict import challenger, live, persist, score
    from ideation.predict.assemble import assemble
    from ideation.report import store as rstore
    from ideation.runs import RunRegistry
    from ideation.trials.registry import load_family

    if horizon not in HORIZONS:
        raise typer.BadParameter(f"horizon must be one of {sorted(HORIZONS)}")
    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    snap = PitStore.latest(settings.data_dir)
    store = PitStore(snap.snapshot_dir, mode="live")
    ucfg = UniverseConfig.from_methodology(meth.data)
    anchor = live.live_anchor(store) if as_of == "latest" else store.calendar.snap_anchor(date.fromisoformat(as_of))
    horizons = HORIZONS[horizon]
    specs = {h: {s.trial_id: s for s in load_family(SHIPPED[h][0], settings.config_dir)} for h in horizons}
    params = {"horizons": list(horizons), "ens": bool(ens and "3M" in horizons), "derived": store.derived_sha(),
              "models": {h: [specs[h][SHIPPED[h][1]].config_hash, specs[h][SHIPPED[h][2]].config_hash] for h in horizons}}  # fmt: skip
    out_dir = settings.data_dir / "outputs" / snap.snap_id
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        with RunRegistry(conn).track("predict", config_hash=meth.config_hash, as_of=anchor, snap_id=snap.snap_id,
                                     params=params, force=force) as run:  # fmt: skip
            if run.skipped:
                console.print(f"skipped: identical predict run {run.record.stats} already ok (use --force)")
                return
            ld = live.build_live(store, reg, ucfg, anchor)
            info = live.security_info(ld)
            stats: dict[str, Any] = {"as_of": str(anchor), "snap_id": snap.snap_id, "regime": ld.tech.labels,
                                     "build_s": round(time.perf_counter() - t0, 1), "horizons": {}}  # fmt: skip
            n_rows = 0
            for h in horizons:
                fam, ship, chal_id = SHIPPED[h]
                tidx = rstore.trial_index(conn, fam).set_index("name")
                spec, chal_spec = specs[h][ship], specs[h][chal_id]
                oos = rstore.oos(conn, int(tidx.loc[ship, "trial_id"]))
                counted = calmod.counted_anchors(oos["anchor"], spec.config, h)
                lc = calmod.fit_live(oos, counted, h, anchor)
                if h == "1Y":
                    base = live.frame_1y(ld, reg)
                    sc = score.score_1y(base, reg, spec, anchor)
                    ood = score.ood_guard_1y(out_dir, reg, spec)
                    chal = challenger.challenger_1y(score.score_1y(base, reg, chal_spec, anchor), base)
                    cinfo: dict[str, Any] = {"model": chal_id, "fit": False}
                else:
                    base = live.frame_3m(ld)
                    sc = score.score_3m(base, reg, spec)
                    ood = score.ood_guard_3m(out_dir, reg, spec)
                    chal, cinfo = None, {"model": chal_id, "skipped": "--no-ens"}
                    if ens:
                        mem, cinfo = challenger.fit_ens(out_dir, settings.data_dir, reg, base, anchor, settings.config_dir)
                        chal = challenger.challenger_3m(mem, base)
                        cinfo["model"] = chal_id
                df = assemble(sc, base, info, lc, ood, reg, ld.tech.labels, ld.tech.rrg, chal)
                if "contrib_sum_err" in df and df["contrib_sum_err"].abs().max() > 1e-9:
                    raise RuntimeError("driver contributions do not sum to the score")
                rows = persist.prediction_rows(df, run.run_id, _model_id(conn, spec.config_hash, h), h, anchor, ship)
                n_rows += persist.write_predictions(conn, rows)
                stats["horizons"][h] = {
                    "model": ship, "config_hash": spec.config_hash[:12], "n_scored": len(df), "n_persisted": len(rows),
                    "n_ood": int(df["ood"].sum()), "calibration": lc.describe(), "ood_guard": ood.describe(),
                    "challenger": cinfo,
                }  # fmt: skip
            run.stats.update(stats)
            run.stats["rows"] = n_rows
            run.stats["seconds"] = round(time.perf_counter() - t0, 1)
    console.print(f"[green]predict ok[/green] run {run.run_id} as of {anchor}: {n_rows} prediction rows, "
                  f"{time.perf_counter() - t0:.0f} s")  # fmt: skip
    for h, s in stats["horizons"].items():
        console.print(f"  {h}: {s['model']} scored {s['n_scored']}, OOD {s['n_ood']}, calibration anchors "
                      f"{len(s['calibration']['anchors'])}")  # fmt: skip


_ = pd
