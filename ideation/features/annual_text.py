# ruff: noqa: E501
"""Company-level point-in-time annual-report features (05 §12 item 2, PT3). Input: one ``PitView`` plus the members.

Leakage rules (same discipline as ``features/text.py``):

* **Visibility is ``available_from`` only.** ``PitView.annual_docs`` returns reports whose ``available_from <= A``, where
  ``available_from = max(fiscal-year end + 180 d, parsed filing / AGM date + 1)`` (``ideation/text/annual.py``). The
  builder re-derives nothing and asserts ``available_from <= A`` for every report it used.
* ``fiscal_year`` is a label and is not read. Windows use ``fy_end``: ``cur`` = the visible quality-OK report with the
  latest ``fy_end`` >= A - 600 d; ``prev`` = the visible quality-OK report whose ``fy_end`` lies 300..430 d before cur's.
* No fill. A name without ``cur`` has NaN in every column (``isna_ar`` = 1); without ``prev``, NaN in every ``d_ar_*``
  (``isna_ar_prev`` = 1; also 1 when there is no ``cur``, so a missing row and a missing ``prev`` agree); a feature whose source section was not found in the report is NaN.
* ``<feature>_rg`` = rank-gauss within the covered members of the anchor (NaN elsewhere); convenience only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import yaml

from ideation.features.registry import Registry
from ideation.pit.contract import LeakageError
from ideation.rank.transform import rank_gauss

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

SOURCE = "features_annual_text.yaml"
IDENTITY_COLS = ("anchor", "security_id", "company_id")
FLAG_COLS = ("isna_ar", "isna_ar_prev")
META_COLS = ("ar_fy_end", "ar_available_from", "ar_prev_fy_end", "ar_avail_basis")
LEVEL_SRC = {"ar_tone": "mda_tone_net", "ar_unc": "mda_unc_r", "ar_aud": "aud_flag", "ar_rpt": "rpt_r", "ar_cl": "cl_r"}
DOC_COLS = ("company_id", "fy_end", "doc_ok", "avail_basis", *LEVEL_SRC.values())


def load_params(config_dir: Path | None = None) -> dict[str, int]:
    if config_dir is None:
        from ideation.config import REPO_ROOT

        config_dir = REPO_ROOT / "config"
    p = yaml.safe_load((Path(config_dir) / SOURCE).read_text(encoding="utf-8"))["params"]
    return {k: int(v) for k, v in p.items()}


def _pick_cur_prev(docs: pd.DataFrame, anchor: date, par: dict[str, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = docs.assign(fe=pd.to_datetime(docs["fy_end"]), af=pd.to_datetime(docs["available_from"]))
    a = pd.Timestamp(anchor)
    if len(d) and (d["af"] > a).any():  # belt and braces: the view guard already guarantees this
        raise LeakageError(f"annual report available_from {d['af'].max().date()} > anchor {anchor}")
    recent = d[d["fe"] >= a - pd.Timedelta(days=par["cur_max_age_days"])]
    recent = recent.sort_values(["company_id", "fe", "af"], kind="stable")
    cur = recent.drop_duplicates("company_id", keep="last").set_index("company_id", drop=False)
    cand = d.merge(
        cur[["fe"]].rename(columns={"fe": "cur_fe"}).reset_index(names="company_id"), on="company_id", how="inner"
    )
    gap = (cand["cur_fe"] - cand["fe"]).dt.days
    cand = cand[(gap >= par["prev_min_gap_days"]) & (gap <= par["prev_max_gap_days"])]
    cand = cand.sort_values(["company_id", "fe", "af"], kind="stable")
    prev = cand.drop_duplicates("company_id", keep="last").set_index("company_id", drop=False)
    return cur, prev


def _day(x: Any) -> date | None:
    return None if pd.isna(x) else pd.Timestamp(x).date()


def build_annual(
    view: PitView, members: pd.DataFrame, reg: Registry, params: dict[str, int] | None = None
) -> pd.DataFrame:
    """One row per member security: identity, 10 raw features, their ``_rg`` scores, 2 flags, provenance dates."""
    par = params or load_params()
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    ids = pd.Index(sorted(mem["company_id"].unique()))
    docs = view.annual_docs(DOC_COLS)
    docs = docs[docs["company_id"].isin(ids) & (docs["doc_ok"] == 1) & docs["fy_end"].notna()]
    cur, prev = _pick_cur_prev(docs, view.anchor, par)
    cur, prev = cur.reindex(ids), prev.reindex(ids)
    f = pd.DataFrame(index=ids)
    for name, src in LEVEL_SRC.items():
        f[name] = cur[src].astype("float64")
        f[f"d_{name}"] = cur[src].astype("float64") - prev[src].astype("float64")
    has_cur, has_prev = cur["fy_end"].notna(), prev["fy_end"].notna()
    f["isna_ar"] = (~has_cur).astype("int8")
    f["isna_ar_prev"] = (~has_prev).astype("int8")  # 1 whenever there is no previous report, incl. no current one
    f["ar_fy_end"] = [_day(x) for x in cur["fy_end"]]
    f["ar_available_from"] = [_day(x) for x in cur["available_from"]]
    f["ar_prev_fy_end"] = [_day(x) for x in prev["fy_end"]]
    f["ar_avail_basis"] = cur["avail_basis"].to_numpy()
    names = reg.names(source=SOURCE)
    mem = mem.merge(f, left_on="company_id", right_index=True, how="left", validate="many_to_one")
    for n in names:
        per_company = f[n].dropna()
        z = rank_gauss(per_company) if len(per_company) else per_company
        mem[f"{n}_rg"] = mem["company_id"].map(z).astype("float64")
    mem.insert(0, "anchor", view.anchor)
    cols = [*IDENTITY_COLS, *names, *(f"{n}_rg" for n in names), *FLAG_COLS, *META_COLS]
    out = mem[cols].sort_values("security_id").reset_index(drop=True)
    if out["ar_available_from"].notna().any():
        mx = max(x for x in out["ar_available_from"] if pd.notna(x))
        if mx > view.anchor:
            raise LeakageError(f"annual feature built from a report available_from {mx} > anchor {view.anchor}")
    return out


def coverage_row(anchor: date, mem: pd.DataFrame, feats: pd.DataFrame) -> dict[str, Any]:
    """Universe-restricted coverage: share of U_score with ``cur`` and with ``cur`` + ``prev``, plus per-feature
    non-NaN counts (``mem`` is the universe member frame of the anchor)."""
    f = feats.set_index("security_id")
    ids = mem.loc[mem["in_u_score"].fillna(False).astype(bool), "security_id"]
    g = f.reindex(ids)
    row: dict[str, Any] = {"anchor": anchor, "n_u": len(ids)}
    row["u_cur"] = int((g["isna_ar"] == 0).sum())
    row["u_cur_prev"] = int(((g["isna_ar"] == 0) & (g["isna_ar_prev"] == 0)).sum())
    for n in LEVEL_SRC:
        row[f"nn_{n}"] = int(g[n].notna().sum())
        row[f"nn_d_{n}"] = int(g[f"d_{n}"].notna().sum())
    return row


__all__ = ["SOURCE", "build_annual", "coverage_row", "load_params"]
