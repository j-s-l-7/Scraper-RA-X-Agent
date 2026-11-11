"""Company-level point-in-time text features (05 §7.2-7.3, PT2). Input: one ``PitView`` plus the member frame.

Leakage rules (the core requirement):

* **Visibility is ``available_from`` only.** ``PitView.text_docs`` returns documents whose ``available_from <= A``,
  where PT1 set ``available_from = max(first day of the month after doc_date, filed_date + 1, matched results event
  + 1, statutory floor for non-exchange sources)`` (05 §7.2). The builder re-derives nothing and asserts
  ``available_from <= A`` for every document it used.
* ``doc_date`` is month-granular (always the 1st). It is used only to build the windows, never to decide visibility:
  ``cur`` = the visible transcript with the latest ``doc_date`` >= A - 200 d; ``prev`` = the visible transcript with
  the latest ``doc_date`` in [cur - 400 d, cur - 60 d]. A document that exists in the corpus but is not yet visible
  can never become ``cur`` or ``prev``.
* No fill. A name without ``cur`` has NaN in every transcript column (``isna_text`` = 1); a name without ``prev`` has
  NaN in every ``d_*`` column (``isna_text_prev`` = 1). Presentations (``ppt_*``) follow the same logic on
  ``concall_ppt``.
* ``<feature>_rg`` = rank-gauss within the covered members of the anchor (NaN elsewhere). It is a convenience; the
  trial's R1 transform on the covered subset is the modelling path.

Features are company-level: every security of a company carries the company's values.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import yaml

from ideation.features.registry import Registry
from ideation.pit.contract import LeakageError
from ideation.rank.transform import rank_gauss

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

SOURCE = "features_text.yaml"
IDENTITY_COLS = ("anchor", "security_id", "company_id")
FLAG_COLS = ("isna_text", "isna_text_prev", "isna_ppt")
META_COLS = ("text_doc_date", "text_available_from", "text_prev_doc_date", "text_source")
DOC_COLS = (
    "company_id", "doc_type", "doc_date", "source", "qa_ok", "tone_net", "guide_net", "unc_r", "gdn_r", "capex_r",
    "qa_tone_net", "qa_minus_prep_tone",
)  # fmt: skip


def load_params(config_dir: Path | None = None) -> dict[str, int]:
    if config_dir is None:
        from ideation.config import REPO_ROOT

        config_dir = REPO_ROOT / "config"
    p = yaml.safe_load((Path(config_dir) / SOURCE).read_text(encoding="utf-8"))["params"]
    return {k: int(v) for k, v in p.items()}


def _pick_cur_prev(docs: pd.DataFrame, anchor: date, par: dict[str, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per company the ``cur`` row and its ``prev`` row (indexed by company_id). ``docs`` is already visible."""
    d = docs.assign(dd=pd.to_datetime(docs["doc_date"]), af=pd.to_datetime(docs["available_from"]))
    a = pd.Timestamp(anchor)
    if len(d) and (d["af"] > a).any():  # belt and braces: the view guard already guarantees this
        raise LeakageError(f"text document available_from {d['af'].max().date()} > anchor {anchor}")
    recent = d[d["dd"] >= a - pd.Timedelta(days=par["cur_max_age_days"])]
    recent = recent.sort_values(["company_id", "dd", "af"], kind="stable")
    cur = recent.drop_duplicates("company_id", keep="last").set_index("company_id", drop=False)
    cand = d.merge(
        cur[["dd"]].rename(columns={"dd": "cur_dd"}).reset_index(names="company_id"), on="company_id", how="inner"
    )
    gap = (cand["cur_dd"] - cand["dd"]).dt.days
    cand = cand[(gap >= par["prev_min_gap_days"]) & (gap <= par["prev_max_gap_days"])]
    cand = cand.sort_values(["company_id", "dd", "af"], kind="stable")
    prev = cand.drop_duplicates("company_id", keep="last").set_index("company_id", drop=False)
    return cur, prev


def _day(x: Any) -> date | None:
    return None if pd.isna(x) else pd.Timestamp(x).date()


def build_text(
    view: PitView, members: pd.DataFrame, reg: Registry, params: dict[str, int] | None = None
) -> pd.DataFrame:
    """One row per member security: identity, 11 raw features, their ``_rg`` scores, 3 flags, provenance dates.

    ``members`` needs ``security_id`` and ``company_id`` (rows without a company are dropped: no text is attributable).
    """
    par = params or load_params()
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    ids = pd.Index(sorted(mem["company_id"].unique()))
    docs_t = view.text_docs("concall_transcript", DOC_COLS)
    docs_p = view.text_docs("concall_ppt", DOC_COLS)
    docs_t = docs_t[docs_t["company_id"].isin(ids)]
    docs_p = docs_p[docs_p["company_id"].isin(ids)]
    cur, prev = _pick_cur_prev(docs_t, view.anchor, par)
    pcur, pprev = _pick_cur_prev(docs_p, view.anchor, par)
    cur, prev = cur.reindex(ids), prev.reindex(ids)
    pcur, pprev = pcur.reindex(ids), pprev.reindex(ids)

    qa = cur["qa_ok"].fillna(0).astype(float) == 1.0
    f = pd.DataFrame(index=ids)
    f["d_tone"] = cur["tone_net"] - prev["tone_net"]
    f["d_guide"] = cur["guide_net"] - prev["guide_net"]
    f["d_unc"] = cur["unc_r"] - prev["unc_r"]
    f["tone_lvl"] = cur["tone_net"]
    f["guide_lvl"] = cur["guide_net"]
    f["gdn_r"] = cur["gdn_r"]
    f["capex_r"] = cur["capex_r"]
    f["qa_tone"] = cur["qa_tone_net"].where(qa)
    f["qa_minus_prep"] = cur["qa_minus_prep_tone"].where(qa)
    f["ppt_d_guide"] = pcur["guide_net"] - pprev["guide_net"]
    f["ppt_capex_r"] = pcur["capex_r"]
    has_cur, has_prev, has_ppt = cur["doc_date"].notna(), prev["doc_date"].notna(), pcur["doc_date"].notna()
    f = f.astype("float64")
    f["isna_text"] = (~has_cur).astype("int8")
    f["isna_text_prev"] = (has_cur & ~has_prev).astype("int8")
    f["isna_ppt"] = (~has_ppt).astype("int8")
    f["text_doc_date"] = [_day(x) for x in cur["doc_date"]]
    f["text_available_from"] = [_day(x) for x in cur["available_from"]]
    f["text_prev_doc_date"] = [_day(x) for x in prev["doc_date"]]
    f["text_source"] = cur["source"].to_numpy()

    names = reg.names(source=SOURCE)
    mem = mem.merge(f, left_on="company_id", right_index=True, how="left", validate="many_to_one")
    for n in names:  # rank-gauss within the covered members of this anchor; one value per company, not per security
        per_company = f[n].dropna()
        z = rank_gauss(per_company) if len(per_company) else per_company
        mem[f"{n}_rg"] = mem["company_id"].map(z).astype("float64")
    mem.insert(0, "anchor", view.anchor)
    cols = [*IDENTITY_COLS, *names, *(f"{n}_rg" for n in names), *FLAG_COLS, *META_COLS]
    out = mem[cols].sort_values("security_id").reset_index(drop=True)
    if out["text_available_from"].notna().any():
        mx = max(x for x in out["text_available_from"] if x is not None)
        if mx > view.anchor:
            raise LeakageError(f"text feature built from a document available_from {mx} > anchor {view.anchor}")
    return out


def coverage_row(anchor: date, mem: pd.DataFrame, feats: pd.DataFrame) -> dict[str, Any]:
    """Universe-restricted coverage: share of U_score (1Y pool) and U_score_T (tactical pool) with ``cur`` and with
    ``cur`` + ``prev`` (transcripts), and with a presentation. ``mem`` is the universe member frame of the anchor."""
    f = feats.set_index("security_id")
    row: dict[str, Any] = {"anchor": anchor}
    for label, flag in (("u", "in_u_score"), ("ut", "in_u_score_t")):
        ids = mem.loc[mem[flag].fillna(False).astype(bool), "security_id"]
        g = f.reindex(ids)
        n = len(ids)
        row[f"n_{label}"] = n
        row[f"{label}_cur"] = int((g["isna_text"] == 0).sum())
        row[f"{label}_cur_prev"] = int(((g["isna_text"] == 0) & (g["isna_text_prev"] == 0)).sum())
        row[f"{label}_ppt"] = int((g["isna_ppt"] == 0).sum())
    return row


# ── multi-call trends (05 §12 item 2, PT3) ───────────────────────────────────────────────────────────────────
TREND_SOURCE = "features_text_trend.yaml"
TREND_FLAG = "isna_trend"
TREND_META = ("trend_last_doc_date", "trend_last_available_from", "trend_n_calls")


def load_trend_params(config_dir: Path | None = None) -> dict[str, int]:
    if config_dir is None:
        from ideation.config import REPO_ROOT

        config_dir = REPO_ROOT / "config"
    p = yaml.safe_load((Path(config_dir) / TREND_SOURCE).read_text(encoding="utf-8"))["params"]
    return {k: int(v) for k, v in p.items()}


def _slope(x: np.ndarray, y: np.ndarray) -> float:
    ok = ~np.isnan(y)
    x, y = x[ok], y[ok]
    if len(x) < 2:
        return float("nan")
    xc = x - x.mean()
    den = float((xc**2).sum())
    return float((xc * (y - y.mean())).sum() / den) if den > 0 else float("nan")


def _streak(y: np.ndarray) -> float:
    """Consecutive strictly improving steps counted back from the latest call (NaN breaks the streak)."""
    n = 0
    for i in range(len(y) - 1, 0, -1):
        if np.isnan(y[i]) or np.isnan(y[i - 1]) or not y[i] > y[i - 1]:
            break
        n += 1
    return float(n)


def build_text_trend(
    view: PitView, members: pd.DataFrame, reg: Registry, params: dict[str, int] | None = None
) -> pd.DataFrame:
    """One row per member security: ``tone_slope4``, ``guide_slope4``, ``impr_streak`` (+ ``_rg``), ``isna_trend`` and
    provenance. Uses only transcripts with ``available_from <= A`` (``PitView.text_docs`` guard, asserted again);
    ``doc_date`` (month-granular) only orders the calls and sets the slope's time axis. No fill."""
    par = params or load_trend_params()
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    ids = pd.Index(sorted(mem["company_id"].unique()))
    docs = view.text_docs("concall_transcript", ("company_id", "doc_date", "tone_net", "guide_net"))
    docs = docs[docs["company_id"].isin(ids)]
    a = pd.Timestamp(view.anchor)
    d = docs.assign(dd=pd.to_datetime(docs["doc_date"]), af=pd.to_datetime(docs["available_from"]))
    if len(d) and (d["af"] > a).any():
        raise LeakageError(f"text document available_from {d['af'].max().date()} > anchor {view.anchor}")
    d = d[d["dd"] >= a - pd.Timedelta(days=par["max_age_days"])].sort_values(["company_id", "dd", "af"], kind="stable")
    rows: dict[int, dict[str, Any]] = {}
    for cid, g in d.groupby("company_id", sort=False):
        g = g.tail(par["n_calls"])
        n = len(g)
        if n < par["min_calls"]:
            continue
        x = (g["dd"] - g["dd"].iloc[-1]).dt.days.to_numpy(dtype=float) / 365.25
        tone, guide = g["tone_net"].to_numpy(dtype=float), g["guide_net"].to_numpy(dtype=float)
        rows[int(cid)] = {
            "tone_slope4": _slope(x, tone), "guide_slope4": _slope(x, guide), "impr_streak": _streak(tone),
            "trend_last_doc_date": _day(g["dd"].iloc[-1]), "trend_last_available_from": _day(g["af"].max()),
            "trend_n_calls": n,
        }  # fmt: skip
    f = pd.DataFrame.from_dict(rows, orient="index").reindex(ids)
    names = reg.names(source=TREND_SOURCE)
    for n_ in names:
        f[n_] = f[n_].astype("float64") if n_ in f else float("nan")
    f[TREND_FLAG] = f["trend_n_calls"].isna().astype("int8")
    mem = mem.merge(f, left_on="company_id", right_index=True, how="left", validate="many_to_one")
    for n_ in names:
        per_company = f[n_].dropna()
        z = rank_gauss(per_company) if len(per_company) else per_company
        mem[f"{n_}_rg"] = mem["company_id"].map(z).astype("float64")
    mem.insert(0, "anchor", view.anchor)
    out = mem[[*IDENTITY_COLS, *names, *(f"{n_}_rg" for n_ in names), TREND_FLAG, *TREND_META]]
    out = out.sort_values("security_id").reset_index(drop=True)
    if out["trend_last_available_from"].notna().any():
        mx = max(x for x in out["trend_last_available_from"] if pd.notna(x))
        if mx > view.anchor:
            raise LeakageError(f"text trend built from a document available_from {mx} > anchor {view.anchor}")
    return out


__all__ = ["SOURCE", "build_text", "build_text_trend", "coverage_row", "load_params", "load_trend_params"]
