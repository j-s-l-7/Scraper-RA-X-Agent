"""Analyst surprise as an OUTCOME (05 §5.1, O5a). With ``features/events_vendor.py`` the only module allowed to name
the vendor estimate columns (``BAN_ALLOWLIST``); everything leaving this module uses ``out_beat`` / ``out_surp``.

* Source: ``PitView.earnings_vendor()`` of a LATE view (the snapshot's last session). The view's guard keeps only rows
  with ``available_from = event_date + 1 <= view anchor``, so an event is never used before the day after its results,
  and events after the snapshot date do not exist for us. Rows without a reported EPS or an estimate are dropped.
* Per row (own values only, the stored surprise is ignored): ``surp = clip((rep - est) / max(|est|, 0.05|rep|, 0.01),
  -2, 2)``; ``beat = 1[rep > est]``, a tie counts 0.5.
* Outcome for anchor A: 1Y = mean over the first (up to) 4 events in (A, A + 12M]; 3M = the first event in (A, A + 3M].
  The window must lie inside the observed data (``A + h <= view anchor``), else :class:`WindowNotObservedError`.
* :func:`announcement_check` is the owner-facing "does the surprise sign agree with the announcement return" test.
* :func:`compare_sets` is the set comparison of 05 §5.1 (difference vs the rest, anchor-clustered bootstrap CI).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

EST, REP = "eps_estimate", "eps_reported"
MAX_EVENTS = {"1Y": 4, "3M": 1}
MONTHS = {"1Y": 12, "3M": 3}
OUT_COLS = ["anchor", "security_id", "horizon", "kind", "n_events", "out_beat", "out_surp"]


class WindowNotObservedError(ValueError):
    """The outcome window extends past the last observed session of the snapshot."""


def surprise(est: pd.Series | np.ndarray, rep: pd.Series | np.ndarray) -> np.ndarray:
    est, rep = np.asarray(est, dtype="float64"), np.asarray(rep, dtype="float64")
    denom = np.maximum(np.maximum(np.abs(est), 0.05 * np.abs(rep)), 0.01)
    return np.clip((rep - est) / denom, -2.0, 2.0)


def beat(est: pd.Series | np.ndarray, rep: pd.Series | np.ndarray) -> np.ndarray:
    est, rep = np.asarray(est, dtype="float64"), np.asarray(rep, dtype="float64")
    return np.where(rep > est, 1.0, np.where(rep == est, 0.5, 0.0))


def load_events(view: PitView) -> pd.DataFrame:
    """Reported events with a usable estimate: company_id, event_date, surp, beat (one row per company and date)."""
    raw = view.earnings_vendor()
    df = raw[raw[REP].notna() & raw[EST].notna()]
    df = df.assign(event_date=pd.to_datetime(df["event_date"]))
    consistent = pd.to_datetime(df["available_from"]) == df["event_date"] + pd.Timedelta(days=1)
    n_bad = int((~consistent).sum())  # 05 §5.1: available_from = event_date + 1; a row that disagrees is not trusted
    df = df[consistent]
    df = df.assign(surp=surprise(df[EST], df[REP]), beat=beat(df[EST], df[REP]))
    df = df.drop_duplicates(["company_id", "event_date"], keep="last").sort_values(["company_id", "event_date"])
    out = df[["company_id", "event_date", "surp", "beat"]].reset_index(drop=True)
    out.attrs["n_dropped_availability_mismatch"] = n_bad
    return out


def build_analyst(
    events: pd.DataFrame, members: pd.DataFrame, anchor: date, horizon: str, observed_to: date
) -> pd.DataFrame:
    """Outcome rows (kind 'analyst') for the members (security_id, company_id) with at least one event in the window."""
    a = pd.Timestamp(anchor)
    hi = a + pd.DateOffset(months=MONTHS[horizon])
    if hi > pd.Timestamp(observed_to):
        raise WindowNotObservedError(
            f"{horizon} window of {anchor} ends {hi.date()} after the observed end {observed_to}"
        )
    w = events[(events["event_date"] > a) & (events["event_date"] <= hi)]
    w = w.sort_values(["company_id", "event_date"]).groupby("company_id").head(MAX_EVENTS[horizon])
    g = w.groupby("company_id").agg(n_events=("surp", "size"), out_beat=("beat", "mean"), out_surp=("surp", "mean"))
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    out = mem.merge(g, left_on="company_id", right_index=True, how="inner")
    out = out.assign(anchor=anchor, horizon=horizon, kind="analyst")
    return out[OUT_COLS].sort_values("security_id").reset_index(drop=True)


# ── the owner-facing check: surprise sign vs announcement return ─────────────────────────────────────────────────
def _cr1_slope(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict[str, float]:
    """OLS slope of y on x with intercept and cluster-robust (CR1) t."""
    n = len(x)
    xc = x - x.mean()
    b = float((xc * (y - y.mean())).sum() / (xc**2).sum())
    resid = (y - y.mean()) - b * xc
    score = pd.Series(xc * resid).groupby(cluster).sum().to_numpy()
    g = len(score)
    var = (g / (g - 1)) * ((n - 1) / (n - 2)) * float((score**2).sum()) / float((xc**2).sum()) ** 2
    return {"slope": b, "t": b / float(np.sqrt(var)), "n": n, "clusters": g}


def announcement_check(ev: pd.DataFrame) -> dict[str, Any]:
    """``ev``: events with ``surp``, ``ear_3d`` and ``event_date``. Quarter-clustered tests that the surprise sign
    agrees with the announcement return: (a) ear_3d on sign(surp) (beat vs miss, ties and zero excluded), (b) the
    share of events whose sign(surp) equals sign(ear_3d), (c) pooled Spearman."""
    from ideation.features.events_vendor import clustered_spearman

    ev = ev[np.isfinite(ev["surp"]) & np.isfinite(ev["ear_3d"])]
    q = ev["event_date"].dt.to_period("Q").astype(str).to_numpy()
    sp = clustered_spearman(ev["surp"].to_numpy(), ev["ear_3d"].to_numpy(), q)
    nz = ev["surp"].to_numpy() != 0
    sgn = np.sign(ev["surp"].to_numpy())[nz]
    ear = ev["ear_3d"].to_numpy()[nz]
    reg = _cr1_slope(sgn, ear, q[nz])
    agree = float((sgn == np.sign(ear)).mean())
    return {
        "n_events": len(ev), "n_quarters": int(sp["clusters"]),
        "mean_ear_beat": float(ear[sgn > 0].mean()), "mean_ear_miss": float(ear[sgn < 0].mean()),
        "beat_minus_miss": 2 * reg["slope"], "t_beat_minus_miss": reg["t"],
        "sign_agreement": agree, "spearman_rho": sp["rho"], "t_spearman": sp["t"],
    }  # fmt: skip


# ── set comparison (05 §5.1 report) ──────────────────────────────────────────────────────────────────────────────
def compare_sets(
    df: pd.DataFrame, sets: dict[str, pd.Series], col: str, *, n_boot: int = 2000, seed: int = 0
) -> pd.DataFrame:
    """Per set: covered count, mean of ``col`` in the set and in the rest, difference and an anchor-clustered
    bootstrap 95% CI (resample anchors with replacement; the statistic is the mean of per-anchor differences).
    ``sets`` maps a name to a boolean Series aligned with ``df``; ``df`` needs ``anchor`` and ``col``."""
    rng = np.random.default_rng(seed)
    rows = []
    for name, mask in sets.items():
        m = mask.reindex(df.index).fillna(False).astype(bool)
        g = df.assign(_in=m).dropna(subset=[col])
        per = g.groupby(["anchor", "_in"])[col].mean().unstack("_in").reindex(columns=[True, False])
        per = per.dropna()
        d = (per[True] - per[False]).to_numpy()
        if len(d) == 0:
            rows.append({"set": name, "n": int(m.sum()), "mean_set": np.nan, "mean_rest": np.nan, "diff": np.nan,
                         "ci_lo": np.nan, "ci_hi": np.nan, "anchors": 0})  # fmt: skip
            continue
        boots = rng.choice(d, size=(n_boot, len(d)), replace=True).mean(axis=1)
        rows.append({"set": name, "n": int(g["_in"].sum()), "mean_set": float(g.loc[g["_in"], col].mean()),
                     "mean_rest": float(g.loc[~g["_in"], col].mean()), "diff": float(d.mean()),
                     "ci_lo": float(np.quantile(boots, 0.025)), "ci_hi": float(np.quantile(boots, 0.975)),
                     "anchors": len(d)})  # fmt: skip
    return pd.DataFrame(rows)
