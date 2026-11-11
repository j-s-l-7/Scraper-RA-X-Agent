"""Write bake-off results to ``ideation.*`` through the guarded writer (never ``equity``/``macro``).

* ``experiment_trial``: status, n_oos, ic_series, spread_series (cost-adjusted Q5-Q1 per anchor) per trial; a failed
  trial stores its error in ``ic_series``. Text trials stay ``registered``.
* ``model``: one ``candidate`` row per fitted trial (never ``champion``: promotion is manual, 03 A12).
* ``wf_result``: one row per (model, test anchor), indicative anchor included.
* ``oos_prediction``: the WFO predictions (CPCV predictions live in parquet only; the key allows one row per name).
* ``rank_calibration``: nested and pooled R5/R6 rows per counted test anchor and bucket.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from ideation.bakeoff import metrics as M
from ideation.bakeoff import report as RP
from ideation.config import canonical_json
from ideation.db.writer import Writer
from ideation.trials.registry import TrialSpec


def _none(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if x is pd.NaT:
        return None
    return x


def persist_all(
    settings: Any,
    family: str,
    run_id: int | None,
    specs: list[TrialSpec],
    tds: dict[str, RP.TD],
    missing: dict[str, str],
    ids: dict[str, int],
    fam: dict[str, Any],
    gates: dict[str, dict[str, Any]],
    audits: dict[str, Any],
) -> None:
    by_id = {s.trial_id: s for s in specs}
    with Writer.from_settings(settings).connect() as conn:
        if run_id is None:  # a standalone `backtest report`: tie the rows to the latest finished train run
            row = conn.execute("select max(run_id) from ideation.run where kind = 'train' and status = 'ok'").fetchone()
            run_id = int(row[0]) if row and row[0] else None
        for k, reason in missing.items():
            if reason.startswith("failed") or reason == "no results":
                conn.execute(
                    "update ideation.experiment_trial set status = %s, ic_series = %s, run_id = coalesce(%s, run_id)"
                    " where trial_id = %s and status <> 'ok'",
                    (
                        "failed" if reason.startswith("failed") else "registered",
                        Jsonb({"error": reason}) if reason.startswith("failed") else None,
                        run_id,
                        ids[k],
                    ),
                )
        for k, t in tds.items():
            spec = by_id[k]
            tid = ids[k]
            s = t.stats
            pa = s["per_anchor"]
            ic_series = {str(a.date()): _none(v) for a, v in pa["ic"].items()}
            sp_series = {str(a.date()): _none(v) for a, v in pa["spread_q5_q1_cost_adj"].items()}
            with conn.transaction():
                conn.execute(
                    "update ideation.experiment_trial set status = 'ok', n_oos = %s, ic_series = %s,"
                    " spread_series = %s,"
                    " run_id = coalesce(%s, run_id) where trial_id = %s",
                    (int(t.oos["score"].notna().sum()), Jsonb(ic_series), Jsonb(sp_series), run_id, tid),
                )
                conn.execute(
                    "delete from ideation.wf_result where model_id in (select model_id from ideation.model"
                    " where params->>'config_hash' = %s)",
                    (spec.config_hash,),
                )
                conn.execute("delete from ideation.model where params->>'config_hash' = %s", (spec.config_hash,))
                sel = {
                    "mean_ic": _none(s["summary"].get("mean")),
                    "ci90": [_none(x) for x in s["summary"].get("ci90", ())],
                    "selection_9010": _none(s.get("selection_9010")),
                    "stability": _none(s["stability"]),
                    "dsr_ic": _none(s["dsr_ic"].get("dsr")),
                    "pbo": _none(fam["pbo"]["pbo"]) if fam.get("pbo") else None,
                    "gates": {
                        a: _none(b) if not isinstance(b, (bool, np.bool_)) else bool(b)
                        for a, b in gates.get(k, {}).items()
                    },
                    "champion_by_9010": bool(fam["champion"] == k),
                }
                row = conn.execute(
                    "insert into ideation.model (horizon, family, params, selection, status, run_id)"
                    " values ('1Y', %s, %s, %s, 'candidate', %s) returning model_id",
                    (
                        spec.config.get("learner") or spec.config.get("model") or spec.config.get("consensus"),
                        Jsonb(
                            json.loads(
                                canonical_json(
                                    {
                                        "trial_id": tid,
                                        "trial": k,
                                        "config_hash": spec.config_hash,
                                        "config": spec.config,
                                    }
                                )
                            )
                        ),
                        Jsonb(sel),
                        run_id,
                    ),
                ).fetchone()
                model_id = int(row[0])  # type: ignore[index]
                wf = []
                for a, r in pa.iterrows():
                    wf.append(
                        (
                            model_id,
                            a.date(),
                            int(r["n_labelled"]),
                            int(r["n_universe"]),
                            _none(r["ic"]),
                            _none(r["ic_lo"]),
                            _none(r["ic_hi"]),
                            _none(r["spread_d10_d1"]),
                            _none(r["hit_top"]),
                            _none(r["top_ret"]),
                            _none(r["bottom_ret"]),
                            _none(r["bench_ret"]),
                            None,
                            Jsonb(
                                {
                                    "coverage": _none(r["coverage"]),
                                    "counted": a in set(t.counted),
                                    "spread_q5_q1_cost_adj": _none(r["spread_q5_q1_cost_adj"]),
                                    "top_q5_excess_cost_adj": _none(r["top_q5_excess_cost_adj"]),
                                }
                            ),
                        ),
                    )
                conn.executemany(
                    "insert into ideation.wf_result (model_id, test_anchor, n, n_universe, ic, ic_lo, ic_hi, spread_tb,"
                    " hit_top, top_ret, bottom_ret, bench_ret, unscored_ret, coverage)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    wf,
                )
                conn.execute("delete from ideation.oos_prediction where trial_id = %s", (tid,))
                o = t.oos
                rows = (
                    (
                        tid,
                        r.anchor.date(),
                        int(r.security_id),
                        _none(r.score),
                        _none(r.p),
                        None if pd.isna(r.bucket_dec) else int(r.bucket_dec),
                        _none(r.label),
                        _none(r.ret_total),
                        _none(r.excess_univ_med),
                        r.fold,
                    )
                    for r in o.itertuples(index=False)
                )
                conn.copy_rows(
                    "ideation.oos_prediction",
                    [
                        "trial_id",
                        "anchor",
                        "security_id",
                        "score",
                        "p",
                        "bucket_dec",
                        "label",
                        "ret_total",
                        "excess_univ_med",
                        "fold",
                    ],
                    rows,
                )
                _persist_calibration(conn, spec, t, run_id)
        oos_dir = settings.data_dir / "oos"
        oos_dir.mkdir(parents=True, exist_ok=True)
        for k, t in tds.items():
            t.oos.to_parquet(oos_dir / f"{ids[k]}.parquet", index=False)


def _persist_calibration(conn: Any, spec: TrialSpec, t: RP.TD, run_id: int | None) -> None:
    key = spec.config_hash[:12]
    conn.execute("delete from ideation.rank_calibration where model_key = %s", (key,))
    hist = RP._hist(t)
    rows = []
    for scheme in ("nested", "pooled"):
        for a, cal in t.stats[scheme].items():
            if cal.get("cal") is None:
                continue
            b = M.calibration_bucket_rows(cal, hist)
            for r in b.itertuples(index=False):
                rows.append(
                    (
                        key,
                        "1Y",
                        a.date(),
                        scheme,
                        r.bucket,
                        int(r.n),
                        _none(r.alpha_raw),
                        _none(r.alpha_shrunk),
                        _none(r.alpha_iso),
                        _none(r.conf_lo),
                        _none(r.conf_hi),
                        _none(r.coverage),
                        run_id,
                    )
                )
    if rows:
        conn.executemany(
            "insert into ideation.rank_calibration (model_key, horizon, test_anchor, scheme, bucket, n, alpha_raw,"
            " alpha_shrunk, alpha_iso, conf_lo, conf_hi, coverage, run_id)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            rows,
        )
