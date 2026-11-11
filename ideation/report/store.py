"""Read-only access to the stored results the research report is built from (05 §10.3 MS3).

* trial ledger, OOS predictions, outcomes: ``ideation.*`` through the guarded connection (SELECT only here);
* labels: ONLY through ``load_labels(unsealed_only(...), purpose='report')``, never by reading the parquet directly;
* feature / universe parquet for sector, size and benchmark columns (unsealed anchors only).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.labels.loader import load_labels, unsealed_only

FAMILIES = {"1Y-june": "1Y", "tactical-3M": "3M"}
EXPECTED_N = {"1Y-june": 50, "tactical-3M": 25}


def trial_index(conn: Any, family: str) -> pd.DataFrame:
    rows = conn.execute(
        "select trial_id, (config_json->>'num')::int, config_json->>'id', status, n_oos, ic_series, config_hash, config_json"
        " from ideation.experiment_trial where family = %s order by 2",
        (family,),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["trial_id", "num", "name", "status", "n_oos", "ic_series", "config_hash", "config"])
    return df


def family_n(conn: Any, family: str) -> int:
    return int(conn.execute("select count(*) from ideation.experiment_trial where family = %s", (family,)).fetchone()[0])


def ic_series(row: pd.Series) -> pd.Series:
    d = row["ic_series"] or {}
    s = pd.Series({pd.Timestamp(k): v for k, v in d.items() if v is not None}, dtype=float).sort_index()
    return s


def oos(conn: Any, trial_id: int, anchors: set[pd.Timestamp] | None = None) -> pd.DataFrame:
    rows = conn.execute(
        "select anchor, security_id, score, p, bucket_dec, label, ret_total, excess_univ_med, fold"
        " from ideation.oos_prediction where trial_id = %s order by anchor, security_id",
        (trial_id,),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["anchor", "security_id", "score", "p", "bucket_dec", "label", "ret_total",
                                     "excess_univ_med", "fold"])  # fmt: skip
    df["anchor"] = pd.to_datetime(df["anchor"])
    for c in ("score", "p", "label", "ret_total", "excess_univ_med"):
        df[c] = df[c].astype(float)
    if anchors is not None:
        df = df[df["anchor"].isin(anchors)]
    return df.reset_index(drop=True)


def labels(data_dir: Path, snap: str, horizon: str, anchors: list[pd.Timestamp], family: str) -> pd.DataFrame:
    req = unsealed_only([a.date() for a in anchors], horizon)
    if len(req) != len(anchors):
        raise ValueError("a sealed anchor reached the report layer")
    lab = load_labels(req, horizon, "report", family=family, source=data_dir / "outputs" / snap / "labels.parquet")
    keep = ["anchor", "security_id", "ret_total", "excess_univ_med", "excess_univ_cw", "excess_n500_pr", "label_end"]
    lab = lab[keep].copy()
    lab["anchor"] = pd.to_datetime(lab["anchor"])
    return lab


def universe(data_dir: Path, snap: str) -> pd.DataFrame:
    u = pd.read_parquet(data_dir / "outputs" / snap / "universe_member.parquet",
                        columns=["anchor", "security_id", "symbol", "company_id", "mcap_pit_cr", "adv63_lakh", "in_u_score"])  # fmt: skip
    u["anchor"] = pd.to_datetime(u["anchor"])
    return u


def company_master(data_dir: Path, snap: str) -> pd.DataFrame:
    return pd.read_parquet(data_dir / "snapshots" / snap / "company.parquet",
                           columns=["company_id", "nse_symbol", "name", "sector", "industry_group", "industry", "sub_industry"])  # fmt: skip


def outcomes(conn: Any, kind: str, horizon: str) -> pd.DataFrame:
    rows = conn.execute(
        "select anchor, security_id, n_events, out_beat, out_surp, out_fs_sales, out_fs_pat, out_accel"
        " from ideation.outcome where kind = %s and horizon = %s",
        (kind, horizon),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["anchor", "security_id", "n_events", "out_beat", "out_surp", "out_fs_sales",
                                     "out_fs_pat", "out_accel"])  # fmt: skip
    df["anchor"] = pd.to_datetime(df["anchor"])
    for c in ("out_beat", "out_surp", "out_fs_sales", "out_fs_pat", "out_accel"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def holdout_status(conn: Any) -> dict[str, Any]:
    rows = conn.execute("select family, champion_config_hash, unsealed_at from ideation.holdout_unseal").fetchall()
    return {r[0]: {"champion_config_hash": r[1], "unsealed_at": str(r[2])} for r in rows}


def model_selection(conn: Any, config_hash: str, horizon: str) -> dict[str, Any]:
    r = conn.execute("select selection from ideation.model where params->>'config_hash' = %s and horizon = %s",
                     (config_hash, horizon)).fetchone()  # fmt: skip
    return dict(r[0]) if r and r[0] else {}


def wf_ic(conn: Any, config_hash: str, horizon: str) -> pd.Series:
    rows = conn.execute(
        "select w.test_anchor, w.ic from ideation.wf_result w join ideation.model m using (model_id)"
        " where m.params->>'config_hash' = %s and m.horizon = %s order by 1", (config_hash, horizon)).fetchall()  # fmt: skip
    return pd.Series({pd.Timestamp(a): float(v) for a, v in rows if v is not None}, dtype=float)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


_ = np
