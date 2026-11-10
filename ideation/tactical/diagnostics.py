# ruff: noqa: E501, N803, N806
"""MS2 diagnostics (NOT trials: they do not count toward N, never alter selection, retrain or tune anything).

Tests the skeptic's hypothesis that the 3M tactical IC is a market-beta / volatility bet or a bid-ask-bounce effect in
illiquid names, using the stored OOS scores only:

1. beta / vol neutralisation: IC of a score against returns residualised on PIT beta (B), beta + vol (BV) and beta + vol
   + size + liquidity (BVLS); the score residualised on the same controls; within-tercile IC (beta, vol).
2. entry-lag robustness: the 3M label re-entered at the close of the 2nd and 5th good session after the anchor (the real
   ``labels.build`` machinery, in memory, nothing written; exit = the unchanged ``label_end``).
3. IC by ADV / market-cap tercile and the standalone IC of every T0 feature.

Controls are PIT: ``beta_252`` (daily OLS beta on the Nifty 500 over the trailing 252 sessions to the anchor, built by
``features.technical``), ``rvol_63`` / ``idio_vol`` (trailing), ``mcap_pit_cr`` and ``adv63_lakh`` (as of the anchor).
Labels are read only through ``load_labels(unsealed_only(...))`` (``tactical.data.load_panel``); the re-entered labels are
computed from prices for unsealed anchors (<= 2025-06-30) only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.eval.inference import newey_west_t, nw_lags
from ideation.eval.metrics import rank_ic

LAGS = nw_lags(3, 1)
COUNTED_START = pd.Timestamp("2014-01-31")
COUNTED_END = pd.Timestamp("2025-06-30")
CONTROLS = {
    "B": ["beta_252"],
    "BV": ["beta_252", "rvol_63", "idio_vol"],
    "BVLS": ["beta_252", "rvol_63", "idio_vol", "ln_mcap", "ln_adv"],
}
MIN_BUCKET = 25


# ── small statistics ─────────────────────────────────────────────────────────────────────────────────────────
def summ(s: pd.Series) -> dict[str, float]:
    """Mean, Newey-West t (lag 2, 3M monthly) and n of a per-anchor series."""
    v = pd.Series(s, dtype=float).dropna()
    if len(v) < 3:
        return {"mean": float("nan"), "t": float("nan"), "n": len(v)}
    return {"mean": float(v.mean()), "t": newey_west_t(v, LAGS), "n": len(v)}


def ic_series(df: pd.DataFrame, score: str, target: str) -> pd.Series:
    """Rank IC per anchor of column ``score`` against ``target``."""
    out = {a: rank_ic(g[score], g[target]) for a, g in df.groupby("anchor", sort=True)}
    return pd.Series(out, dtype=float)


def winsor(x: pd.Series, lo: float = 0.01, hi: float = 0.99) -> pd.Series:
    return x.clip(x.quantile(lo), x.quantile(hi))


def _xs_residual(y: pd.Series, X: pd.DataFrame) -> pd.Series:
    """OLS residual of ``y`` on ``X`` (+ intercept) within one cross-section; NaN controls take the cross-sectional median."""
    Xf = X.apply(lambda c: c.fillna(c.median()))
    Xf = Xf.loc[:, Xf.notna().all() & (Xf.std() > 0)]
    ok = y.notna()
    A = np.column_stack([np.ones(len(Xf)), Xf.to_numpy(float)])
    beta, *_ = np.linalg.lstsq(A[ok.to_numpy()], y[ok].to_numpy(float), rcond=None)
    return pd.Series(np.where(ok, y.to_numpy(float) - A @ beta, np.nan), index=y.index)


def residualize(df: pd.DataFrame, col: str, controls: list[str], *, wins: bool = True) -> pd.Series:
    """Per-anchor residual of ``col`` (winsorised 1/99 first when ``wins``) on ``controls``."""
    parts = []
    for _, g in df.groupby("anchor", sort=False):
        y = winsor(g[col]) if wins else g[col]
        parts.append(_xs_residual(y, g[controls]))
    return pd.concat(parts).reindex(df.index)


def add_controls(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ln_mcap"] = np.log(out["mcap_pit_cr"].where(out["mcap_pit_cr"] > 0))
    out["ln_adv"] = np.log(out["adv63_lakh"].where(out["adv63_lakh"] > 0))
    return out


def tercile(df: pd.DataFrame, col: str, n: int = 3) -> pd.Series:
    """Within-anchor bucket 0..n-1 of ``col`` by rank (0 = lowest)."""
    r = df.groupby("anchor")[col].rank(method="first", pct=True)
    return np.minimum((r * n).apply(np.ceil) - 1, n - 1).where(df[col].notna())


def within_bucket_ic(df: pd.DataFrame, score: str, target: str, by: str, n: int = 3) -> pd.DataFrame:
    """Per-anchor IC inside each within-anchor bucket of ``by`` (columns b0..b{n-1}) and their average ``avg``."""
    b = tercile(df, by, n)
    rows = {}
    for a, g in df.groupby("anchor", sort=True):
        r = {}
        for k in range(n):
            gk = g[b.loc[g.index] == k]
            r[f"b{k}"] = rank_ic(gk[score], gk[target]) if len(gk) >= MIN_BUCKET else np.nan
        rows[a] = r
    out = pd.DataFrame(rows).T
    out["avg"] = out.mean(axis=1, skipna=False)
    return out


def by_year(s: pd.Series) -> pd.DataFrame:
    s = pd.Series(s, dtype=float).dropna()
    yrs = pd.to_datetime(s.index).year
    return pd.DataFrame({y: summ(g) for y, g in s.groupby(yrs)}).T


# ── neutralisation (item 1) ──────────────────────────────────────────────────────────────────────────────────
def neutral_ic_table(df: pd.DataFrame, score: str = "score", ret: str = "ret_3m") -> dict[str, pd.Series]:
    """Per-anchor IC series of one scored frame (``df`` has anchor, score, ret, the PIT controls).

    ``raw`` ; ``res_B/BV/BVLS`` = IC against the return residualised on the controls ; ``sres_BVLS`` = IC of the score
    residualised on the controls against the raw return ; ``wb_avg`` / ``wv_avg`` = average of the three within-beta /
    within-trailing-vol tercile ICs (``wb0..2``, ``wv0..2`` are the terciles)."""
    d = add_controls(df)
    out = {"raw": ic_series(d, score, ret)}
    for k, cols in CONTROLS.items():
        d[f"_r_{k}"] = residualize(d, ret, cols)
        out[f"res_{k}"] = ic_series(d, score, f"_r_{k}")
    d["_s_BVLS"] = residualize(d, score, CONTROLS["BVLS"], wins=False)
    out["sres_BVLS"] = ic_series(d, "_s_BVLS", ret)
    for tag, by in (("wb", "beta_252"), ("wv", "rvol_63")):
        w = within_bucket_ic(d, score, ret, by)
        out[f"{tag}_avg"] = w["avg"]
        for k in range(3):
            out[f"{tag}{k}"] = w[f"b{k}"]
    return out


def exposure_table(df: pd.DataFrame, score: str = "score") -> pd.DataFrame:
    """Per-anchor rank correlation of the score with beta / vol / size / ADV, and mean beta of the top vs bottom quintile."""
    d = add_controls(df)
    rows = {}
    for a, g in d.groupby("anchor", sort=True):
        q = g[score].rank(pct=True)
        rows[a] = {
            "corr_beta": rank_ic(g[score], g["beta_252"]),
            "corr_vol": rank_ic(g[score], g["rvol_63"]),
            "corr_lnmcap": rank_ic(g[score], g["ln_mcap"]),
            "corr_lnadv": rank_ic(g[score], g["ln_adv"]),
            "beta_q5_minus_q1": g.loc[q > 0.8, "beta_252"].mean() - g.loc[q <= 0.2, "beta_252"].mean(),
        }
    return pd.DataFrame(rows).T


def market_series(panel_rows: pd.DataFrame, ret: str = "ret_3m") -> pd.Series:
    """Equal-weight (winsorised) 3M return of the U_score_T cross-section per anchor: the 'market' of the universe."""
    return panel_rows.groupby("anchor")[ret].apply(lambda s: winsor(s).mean())


# ── entry-lag robustness (item 2) ────────────────────────────────────────────────────────────────────────────
def kth_session_after(good: np.ndarray, anchor: Any, k: int) -> Any:
    """k-th good session strictly after the anchor (k = 1 is the s1 of 03 §C3)."""
    i = int(np.searchsorted(good, np.datetime64(pd.Timestamp(anchor).date(), "D"), side="right")) + k - 1
    return good[i].astype(object) if i < len(good) else None


def lagged_returns(store: Any, universe: pd.DataFrame, anchors: list[Any], ks: tuple[int, ...] = (1, 2, 5)) -> pd.DataFrame:
    """3M total return with entry at the close of the k-th good session after the anchor (first trade on or after it, as
    03 §C3), exit at the unchanged ``label_end``. In memory only: the stored labels and the DB are not touched.
    Columns: anchor, security_id, ret_s{k}, exit_s{k}. Only unsealed anchors are accepted."""
    from ideation.labels.build import HORIZON_MONTHS, LabelSpec, compute_labels
    from ideation.labels.loader import unsealed_only

    dates = [pd.Timestamp(a).date() for a in anchors]
    assert unsealed_only(dates, "3M") == dates, "a sealed 3M anchor was requested"
    assert max(dates) <= COUNTED_END.date(), "anchor beyond the counted OOS end"
    cal = store.calendar
    uni = universe[universe["anchor"].isin(set(dates))]
    out: pd.DataFrame | None = None
    for k in ks:
        specs = []
        for a in dates:
            end = cal.exit_session(a, HORIZON_MONTHS["3M"])
            ent = kth_session_after(cal.good, a, k)
            if end is not None and ent is not None and end >= ent:
                specs.append(LabelSpec(a, "3M", ent, end))
        lab = compute_labels(store, uni, specs)
        lab["anchor"] = pd.to_datetime(lab["anchor"]).dt.normalize()
        lab = lab[["anchor", "security_id", "ret_total", "exit_type"]].rename(
            columns={"ret_total": f"ret_s{k}", "exit_type": f"exit_s{k}"}
        )
        out = lab if out is None else out.merge(lab, on=["anchor", "security_id"], how="outer")
    assert out is not None
    return out


def entry_lag_table(df: pd.DataFrame, ks: tuple[int, ...] = (1, 2, 5)) -> dict[str, pd.Series]:
    """IC series of ``score`` vs the re-entered 3M returns (raw and BVLS-residualised) and vs the early (s1..sk) return.

    ``df`` carries ret_s{k}; the early return of the first k-1 sessions is (1+ret_s1)/(1+ret_sk)-1 on rows whose exit is
    not a distress/zero case (so the ratio is defined). All series are on the rows where every ret_s{k} exists."""
    d = add_controls(df)
    need = [f"ret_s{k}" for k in ks]
    d = d[d[need].notna().all(axis=1)].copy()
    out = {}
    for k in ks:
        out[f"raw_s{k}"] = ic_series(d, "score", f"ret_s{k}")
        d[f"_r{k}"] = residualize(d, f"ret_s{k}", CONTROLS["BVLS"])
        out[f"bvls_s{k}"] = ic_series(d, "score", f"_r{k}")
    if 1 in ks:
        for k in ks:
            if k == 1:
                continue
            ok = (d["ret_s1"] > -0.999) & (d[f"ret_s{k}"] > -0.999)
            e = ((1 + d["ret_s1"]) / (1 + d[f"ret_s{k}"]) - 1).where(ok)
            out[f"early_s1_to_s{k}"] = ic_series(d.assign(_e=e), "score", "_e")
    return out


# ── buckets and feature IC (item 3) ──────────────────────────────────────────────────────────────────────────
def bucket_table(df: pd.DataFrame, by: str, score: str = "score", ret: str = "ret_3m") -> pd.DataFrame:
    """Mean IC and NW t in each within-anchor tercile of ``by`` plus top-minus-bottom (paired by anchor)."""
    w = within_bucket_ic(df, score, ret, by)
    rows = {f"T{k + 1}": summ(w[f"b{k}"]) for k in range(3)}
    rows["T3-T1"] = summ(w["b2"] - w["b0"])
    return pd.DataFrame(rows).T


def feature_ic_table(df: pd.DataFrame, cols: list[str], priors: dict[str, Any], resid_col: str = "_res_B") -> pd.DataFrame:
    """Standalone rank IC of every raw feature vs the 3M return (and vs the beta-residual return), with sub-period means."""
    rows = {}
    yr = pd.to_datetime(pd.Series(sorted(df["anchor"].unique()))).dt.year.to_numpy()
    anchors = sorted(df["anchor"].unique())
    for c in cols:
        s = ic_series(df.assign(_f=df[c].astype(float)), "_f", "ret_3m")
        sr = ic_series(df.assign(_f=df[c].astype(float)), "_f", resid_col)
        sm = summ(s)
        yrs = pd.Series(yr, index=anchors)
        pr = priors.get(c)
        rows[c] = {
            "prior": 0 if pr is None or pd.isna(pr) else int(pr),
            "ic": sm["mean"], "nw_t": sm["t"],
            "ic_resB": float(sr.mean()), "ic_2018_19": float(s[yrs.loc[s.index].isin([2018, 2019])].mean()),
            "ic_2020_21": float(s[yrs.loc[s.index].isin([2020, 2021])].mean()),
            "pct_pos": float((s.dropna() > 0).mean()),
        }  # fmt: skip
    t = pd.DataFrame(rows).T
    t["abs_t"] = t["nw_t"].abs()
    return t.sort_values("abs_t", ascending=False).drop(columns="abs_t")


# ── assembly + report ────────────────────────────────────────────────────────────────────────────────────────
def md(df: pd.DataFrame, fmt: str = ".4f") -> str:
    df = df.copy()
    head = "| " + " | ".join([str(df.index.name or ""), *map(str, df.columns)]) + " |"
    sep = "|" + "---|" * (len(df.columns) + 1)
    body = []
    for idx, r in df.iterrows():
        cells = [f"{v:{fmt}}" if isinstance(v, (float, np.floating)) and not pd.isna(v) else ("" if pd.isna(v) else str(v)) for v in r]
        body.append("| " + " | ".join([str(idx), *cells]) + " |")
    return "\n".join([head, sep, *body])


def load_scored(root: Path, members: list[str], raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The five diagnosed scores over the counted OOS: ENS_RANKAVG_T (rebuilt from the 10 member score files exactly as the
    report does), SCR_T0, RIDGE_T0, T_EW_SIGNPRIOR and MOM_12_1 (raw mom_12_1 over every U_score_T row)."""
    from ideation.tactical import evalx

    def one(name: str) -> pd.DataFrame:
        fs = sorted((root / "scores").glob(f"[0-9][0-9]_{name}.parquet"))
        assert len(fs) == 1, (name, fs)
        df = pd.read_parquet(fs[0])
        df["anchor"] = pd.to_datetime(df["anchor"])
        return df[["anchor", "security_id", "score"]]

    mem = {m: one(m) for m in members}
    scores = {"ENS_RANKAVG_T": evalx.consensus_scores(mem)["ENS_RANKAVG_T"]}
    for n in ("SCR_T0", "RIDGE_T0", "T_EW_SIGNPRIOR"):
        scores[n] = mem[n] if n in mem else one(n)
    r = raw[(raw["anchor"] >= COUNTED_START) & (raw["anchor"] <= COUNTED_END)]
    scores["MOM_12_1"] = pd.DataFrame({"anchor": r["anchor"], "security_id": r["security_id"], "score": r["mom_12_1"].astype(float)})
    return scores


MODELS = ("ENS_RANKAVG_T", "SCR_T0", "RIDGE_T0", "T_EW_SIGNPRIOR", "MOM_12_1")
CTRL_COLS = ["beta_252", "rvol_63", "idio_vol", "adv63_lakh", "mcap_pit_cr"]
VARIANTS = ["raw", "res_B", "res_BV", "res_BVLS", "sres_BVLS", "wb_avg", "wv_avg"]


def run(settings: Any, snap: str, *, skip_lag: bool = False) -> tuple[str, dict[str, Any]]:
    """Run all diagnostics; returns (markdown, key numbers)."""
    import yaml

    from ideation.pit.contract import PitStore
    from ideation.tactical.data import load_panel

    out_dir = settings.data_dir / "outputs" / snap
    root = settings.data_dir / "tactical_ms2"
    panel = load_panel(out_dir, snap, settings.config_dir)
    raw = panel.frame
    uni = pd.read_parquet(out_dir / "universe_member.parquet")
    um = uni[["anchor", "security_id", "mcap_pit_cr"]].copy()
    um["anchor"] = pd.to_datetime(um["anchor"]).dt.normalize()
    raw = raw.merge(um, on=["anchor", "security_id"], how="left")
    cfg = yaml.safe_load((settings.config_dir / "trials_tactical.yaml").read_text())
    scored = load_scored(root, cfg["consensus_members"], raw)
    base_cols = ["anchor", "security_id", "ret_3m", *CTRL_COLS]
    rawc = raw[(raw["anchor"] >= COUNTED_START) & (raw["anchor"] <= COUNTED_END)]
    dfs = {m: s.merge(rawc[base_cols], on=["anchor", "security_id"], how="inner") for m, s in scored.items()}
    lines = ["# MS2 diagnostics (not trials; do not count toward N; no retraining or selection)", "",
             f"Snapshot {snap}; counted OOS 2014-01..2025-06; IC = mean per-anchor Spearman IC vs the 3M label; t = Newey-West lag {LAGS}. "
             "Per-year t uses 12 overlapping observations and is only indicative.", ""]
    key: dict[str, Any] = {}

    # reproduce headline
    lines += ["## 0. Reproduction of the MS2 headline", ""]
    rep = pd.DataFrame({m: summ(ic_series(dfs[m], "score", "ret_3m")) for m in MODELS}).T
    lines += [md(rep), "", "MS2_REPORT: ENS 0.1258, SCR 0.1209, RIDGE 0.1195, T_EW 0.1002, MOM 0.0716.", ""]
    key["repro"] = rep["mean"].to_dict()

    # item 1
    lines += ["## 1. Beta / vol neutralisation", "",
              "Controls (PIT, trailing): beta_252 = daily OLS beta to the Nifty 500 over 252 sessions to the anchor (`features.technical`; "
              "NaN -> 1, clipped to [-1, 3]); rvol_63; idio_vol; ln mcap_pit_cr; ln adv63. `res_X` = IC vs the 1/99-winsorised 3M return "
              "residualised per anchor on B = beta, BV = beta+rvol+idio_vol, BVLS = BV+size+ADV. `sres_BVLS` = the score residualised on BVLS vs the raw "
              "return. `wb_avg`/`wv_avg` = mean of the three within-beta / within-trailing-vol tercile ICs. Caveat: beta_252, rvol_63 and idio_vol are "
              "themselves T0 inputs of the models, and daily beta of illiquid names is biased down (asynchronous trading), so beta partly proxies illiquidity.", ""]
    nic: dict[str, dict[str, pd.Series]] = {}
    for m in MODELS:
        nic[m] = neutral_ic_table(dfs[m])
    tab = pd.DataFrame({m: {v: summ(nic[m][v])["mean"] for v in VARIANTS} for m in MODELS})
    tt = pd.DataFrame({m: {v: summ(nic[m][v])["t"] for v in VARIANTS} for m in MODELS})
    surv = tab / tab.loc["raw"]
    lines += ["### Overall mean IC", "", md(tab.T), "", "### Overall NW t", "", md(tt.T, ".2f"), "", "### Share of raw IC surviving", "", md(surv.T, ".2f"), ""]
    key["overall"] = tab.to_dict()
    key["overall_t"] = tt.to_dict()
    terc = pd.DataFrame({m: {f"{t}{k}": summ(nic[m][f"{t}{k}"])["mean"] for t in ("wb", "wv") for k in range(3)} for m in MODELS})
    lines += ["### Within-tercile mean IC (0 = lowest beta / vol)", "", md(terc.T), ""]
    for m in MODELS:
        yr = pd.concat({v: by_year(nic[m][v])["mean"] for v in VARIANTS}, axis=1)
        lines += [f"### Per-year mean IC: {m}", "", md(yr.T, ".3f"), ""]
        if m == "ENS_RANKAVG_T":
            yt = pd.concat({v: by_year(nic[m][v])["t"] for v in VARIANTS}, axis=1)
            lines += ["Per-year NW t (n = 12, indicative): ENS_RANKAVG_T", "", md(yt.T, ".1f"), ""]
        key.setdefault("by_year", {})[m] = yr.to_dict()
    # regime
    ens = dfs["ENS_RANKAVG_T"]
    ex = exposure_table(ens)
    mkt = market_series(rawc).reindex(ex.index)
    ic_e = nic["ENS_RANKAVG_T"]["raw"]
    ic_b = nic["ENS_RANKAVG_T"]["res_BVLS"]
    yr_ex = ex.groupby(pd.to_datetime(ex.index).year).mean()
    lines += ["### Exposure of the champion score and regime dependence", "",
              md(yr_ex.assign(mkt_3m=mkt.groupby(pd.to_datetime(mkt.index).year).mean()), ".3f"), "",
              f"Mean over anchors: corr(score, beta) {ex['corr_beta'].mean():.3f}, corr(score, rvol_63) {ex['corr_vol'].mean():.3f}, "
              f"corr(score, ln mcap) {ex['corr_lnmcap'].mean():.3f}, corr(score, ln ADV) {ex['corr_lnadv'].mean():.3f}; mean beta(Q5) - beta(Q1) {ex['beta_q5_minus_q1'].mean():.3f}.", "",
              f"Correlation of the per-anchor IC with the universe EW 3M return: raw {ic_e.corr(mkt):.3f}, BVLS-residual {ic_b.corr(mkt):.3f}. "
              f"Raw IC when the EW 3M return is < 0 / >= 0: {ic_e[mkt < 0].mean():.4f} (n={int((mkt < 0).sum())}) / {ic_e[mkt >= 0].mean():.4f} (n={int((mkt >= 0).sum())}); "
              f"BVLS-residual IC: {ic_b[mkt < 0].mean():.4f} / {ic_b[mkt >= 0].mean():.4f}.", ""]
    key["exposure"] = {"corr_beta": float(ex["corr_beta"].mean()), "corr_vol": float(ex["corr_vol"].mean()),
                       "corr_lnmcap": float(ex["corr_lnmcap"].mean()), "corr_lnadv": float(ex["corr_lnadv"].mean()),
                       "ic_vs_mkt_corr": float(ic_e.corr(mkt)), "ic_bvls_vs_mkt_corr": float(ic_b.corr(mkt))}

    # item 2
    if not skip_lag:
        lines += ["## 2. Entry-lag robustness", "",
                  "Method: `labels.build.compute_labels` run in memory (nothing written) with `LabelSpec(entry_session = k-th good session after the anchor, "
                  "label_end = the unchanged 03 C3 exit)`; the entry is the first trade on or after that session; k = 1 is the stored s1 baseline, k = 2 delays "
                  "entry by 1 session, k = 5 by 4. The holding period shrinks by the delay. `early_s1_to_sk` = IC against the return earned between the s1 and s_k closes.", ""]
        store = PitStore(settings.data_dir / "snapshots" / snap)
        uni2 = uni.copy()
        uni2["anchor"] = pd.to_datetime(uni2["anchor"]).dt.date
        anchors = sorted(rawc["anchor"].unique())
        lag = lagged_returns(store, uni2, anchors)
        chk = rawc[["anchor", "security_id", "ret_3m"]].merge(lag[["anchor", "security_id", "ret_s1"]], on=["anchor", "security_id"])
        diff = (chk["ret_3m"] - chk["ret_s1"]).abs()
        lines += [f"Validation: recomputed s1 vs stored label on {len(chk)} rows: max |diff| {diff.max():.2e}, rows differing > 1e-9: {int((diff > 1e-9).sum())}.", ""]
        key["lag_validation_maxdiff"] = float(diff.max())
        lt = {}
        lic: dict[str, dict[str, pd.Series]] = {}
        for m in MODELS:
            d = dfs[m].merge(lag, on=["anchor", "security_id"], how="left")
            lic[m] = entry_lag_table(d)
            lt[m] = {k: summ(v)["mean"] for k, v in lic[m].items()}
        ltab = pd.DataFrame(lt)
        lines += ["### Mean IC by entry lag", "", md(ltab.T), "", "### NW t", "",
                  md(pd.DataFrame({m: {k: summ(v)["t"] for k, v in lic[m].items()} for m in MODELS}).T, ".2f"), "",
                  "### Share of s1 IC retained", "",
                  md(pd.DataFrame({m: {"s2/s1": ltab.loc["raw_s2", m] / ltab.loc["raw_s1", m], "s5/s1": ltab.loc["raw_s5", m] / ltab.loc["raw_s1", m],
                                       "bvls_s5/raw_s1": ltab.loc["bvls_s5", m] / ltab.loc["raw_s1", m]} for m in MODELS}).T, ".2f"), ""]
        yl = pd.concat({k: by_year(lic["ENS_RANKAVG_T"][k])["mean"] for k in ("raw_s1", "raw_s2", "raw_s5", "bvls_s5")}, axis=1)
        lines += ["### Per-year mean IC by entry lag: ENS_RANKAVG_T", "", md(yl.T, ".3f"), ""]
        key["lag"] = ltab.to_dict()

    # item 3
    lines += ["## 3. Liquidity, size, standalone features", ""]
    d = add_controls(dfs["ENS_RANKAVG_T"])
    for by, label in (("ln_adv", "ADV63 tercile"), ("ln_mcap", "market-cap tercile")):
        lines += [f"### ENS_RANKAVG_T IC by {label} (T1 = lowest)", "", md(bucket_table(d, by), ".4f"), ""]
    bk = {}
    for m in MODELS:
        dm = add_controls(dfs[m])
        for by in ("ln_adv", "ln_mcap"):
            b = bucket_table(dm, by)
            for t in b.index:
                bk.setdefault(f"{by}:{t}", {})[m] = b.loc[t, "mean"]
    lines += ["### Mean IC by bucket, all five scores", "", md(pd.DataFrame(bk).T), ""]
    key["buckets"] = bk
    rbk = {}
    for by in ("ln_adv", "ln_mcap"):
        dr = d.copy()
        dr["_r"] = residualize(dr, "ret_3m", CONTROLS["BV"])
        w = within_bucket_ic(dr, "score", "_r", by)
        for k in range(3):
            rbk[f"{by}:T{k + 1}"] = summ(w[f"b{k}"])["mean"]
    lines += ["ENS_RANKAVG_T within-bucket IC against the BV-residual return (beta+vol removed): " + ", ".join(f"{k} {v:.4f}" for k, v in rbk.items()), ""]
    key["buckets_resBV"] = rbk
    cols = panel.reg.names(sets=["tech"])
    f = add_controls(rawc.copy())
    f["_res_B"] = residualize(f, "ret_3m", CONTROLS["B"])
    ft = feature_ic_table(f, [c for c in cols if c in f.columns], panel.reg.priors())
    lines += ["### Standalone IC of each T0 feature (raw value vs 3M return, all U_score_T rows, 2014-01..2025-06)", "",
              "`prior` = registry sign (0 = none); `ic_resB` = IC against the beta-residual return.", "", md(ft), ""]
    key["features"] = ft[["ic", "nw_t"]].to_dict()
    return "\n".join(lines), key
