# ruff: noqa: E501
"""P6 (01 §5, 02 P5 row): calibration monotone, conformal coverage, OOD size 0, financial cap, overlay hook,
challenger isolation, continuity, run keys, LLM module (no network), contributions, neutral ranks."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ideation.ideas import llm_summary, overlay, render, select
from ideation.models.phase1 import FixedWeights
from ideation.predict import cal as calmod
from ideation.predict import score
from ideation.rank.conviction import UNSCORABLE
from ideation.runs import compute_run_key

pytestmark = pytest.mark.unit
AS_OF = date(2026, 11, 5)
REGIME = {"trend": "Up", "vol": "Calm", "breadth": "Broad", "narrow_rally": "false", "macro": ""}


def synthetic_oos(n_anchors: int = 8, n: int = 400, seed: int = 0, signal: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_anchors):
        p = (np.arange(n) + 0.5) / n
        ex = signal * (p - 0.5) + rng.normal(0, 0.2, n)
        rows.append(pd.DataFrame({"anchor": pd.Timestamp(2015 + k, 6, 30), "p": p, "excess_univ_med": ex}))
    return pd.concat(rows, ignore_index=True)


def test_calibration_is_monotone_even_for_a_noisy_nonmonotone_history() -> None:
    oos = synthetic_oos(signal=0.01, seed=3)
    lc = calmod.fit_live(oos, sorted(oos["anchor"].unique()), "1Y", date(2025, 1, 1))
    grid = pd.Series(np.linspace(0.001, 0.999, 500))
    a = lc.apply(grid)
    assert (np.diff(a["alpha"].to_numpy()) >= -1e-12).all()
    assert (a["lo"] <= a["alpha"]).all() and (a["alpha"] <= a["hi"]).all()


def test_conformal_band_coverage_is_about_80_percent_out_of_sample() -> None:
    oos = synthetic_oos(n_anchors=10, signal=0.3, seed=1)
    fit, test = oos[oos["anchor"] < pd.Timestamp(2023, 1, 1)], oos[oos["anchor"] >= pd.Timestamp(2023, 1, 1)]
    lc = calmod.fit_live(fit, sorted(fit["anchor"].unique()), "1Y", date(2023, 1, 1))
    out = lc.apply(test["p"].reset_index(drop=True))
    cov = (
        (test["excess_univ_med"].to_numpy() >= out["lo"].to_numpy())
        & (test["excess_univ_med"].to_numpy() <= out["hi"].to_numpy())
    ).mean()
    assert 0.74 <= cov <= 0.86


def test_calibration_refuses_a_sealed_anchor_and_an_empty_history() -> None:
    oos = synthetic_oos(2)
    sealed = pd.Timestamp(2025, 8, 29)
    with pytest.raises(calmod.CalibrationError, match="sealed"):
        calmod.fit_live(oos, [sealed], "3M", date(2026, 11, 5))
    with pytest.raises(calmod.CalibrationError):
        calmod.fit_live(oos, [], "1Y", date(2026, 11, 5))


def test_live_calibration_uses_only_counted_anchors() -> None:
    cfg = {"counted_tests": [2020, 2021]}
    anchors = [pd.Timestamp(y, 6, 28) for y in (2019, 2020, 2021, 2022)]
    assert [a.year for a in calmod.counted_anchors(anchors, cfg, "1Y")] == [2020, 2021]
    cfg3 = {"counted_oos": {"start": "2014-01-31", "end": "2025-06-30"}}
    a3 = [pd.Timestamp(2013, 12, 31), pd.Timestamp(2025, 6, 30), pd.Timestamp(2025, 7, 31)]
    assert [a.date() for a in calmod.counted_anchors(a3, cfg3, "3M")] == [date(2025, 6, 30)]


# ── selection, OOD size 0, conviction, financial cap ────────────────────────────────────────────────────────
def make_pred(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    p = (np.arange(n) + 0.5) / n
    df = pd.DataFrame({
        "company_id": np.arange(1, n + 1), "security_id": np.arange(1, n + 1), "symbol": [f"S{i}" for i in range(n)],
        "score": p, "p": p, "ideation_score": np.round(100 * p), "decile": np.minimum((p * 10).astype(int) + 1, 10),
        "alpha": 0.02 * p, "lo": -0.2, "hi": 0.3, "hit_rate": 0.5, "ood": False, "ood_score": 0.4, "c_conf": 1.0,
        "feat_complete_share": 1.0, "mcap_pit_cr": 5000.0, "adv20_cr": 10.0, "last_close": 200.0, "series": "EQ",
        "industry_group": [f"G{i % 12}" for i in range(n)], "layout": "core", "regime_misaligned": False,
        "beta": rng.uniform(0.5, 1.5, n), "vol": 0.3, "neutral_p_bv": p, "neutral_p_bvls": p, "quadrant": "Leading",
        "rs_ratio": 100.0, "rs_mom": 100.0, "sma50": 190.0, "model": "T", "units": "u", "model_id": 1,
    })  # fmt: skip
    df["flags"] = [[] for _ in range(n)]
    df["drivers"] = [{"top": [], "bottom": [], "by_category": {}} for _ in range(n)]
    df["challenger"] = None
    return df


def run_select(df: pd.DataFrame, **kw) -> dict[str, pd.DataFrame]:
    return select.select_ideas(df, "3M", select.IdeasConfig(**kw), AS_OF, REGIME)


def test_ood_names_are_size_zero_never_ideas_and_listed_separately() -> None:
    df = make_pred()
    top = df["p"].nlargest(5).index
    df.loc[top, "ood"] = True
    sel = run_select(df)
    ideas = set(sel["longs"]["company_id"]) | set(sel["avoid"]["company_id"])
    assert not (set(df.loc[top, "company_id"]) & ideas)
    assert set(sel["ood"]["company_id"]) == set(df.loc[top, "company_id"])
    assert (sel["ood"]["conviction"] == UNSCORABLE).all()


def test_financial_conviction_is_capped_at_medium_even_with_a_consensus_boost() -> None:
    df = make_pred()
    fin = df["p"].nlargest(3).index
    df.loc[fin, "layout"] = "fin_layout"
    sel = run_select(df)
    longs = sel["longs"].set_index("company_id")
    assert set(longs.loc[df.loc[fin, "company_id"], "conviction"]) <= {"Medium", "Low"}
    eff = pd.DataFrame({"company_id": df.loc[fin, "company_id"], "action": "boost", "person": "x", "direction": "bullish",
                        "subject": "s", "thesis": "t", "url": "u", "posted_at": "d", "age_days": 1, "weight": 1.0})  # fmt: skip
    boosted = select.select_ideas(df, "3M", select.IdeasConfig(), AS_OF, REGIME, eff)["longs"].set_index("company_id")
    assert "High" not in set(boosted.loc[df.loc[fin, "company_id"], "conviction"])


def test_conviction_notches_and_base_levels() -> None:
    df = make_pred(1000)
    sel = run_select(df)
    longs = sel["longs"]
    assert set(longs["conviction"]) <= {"High", "Medium", "Low"}
    assert (longs["rank"] == np.arange(1, len(longs) + 1)).all()
    assert (np.diff(longs["p"].to_numpy()) <= 0).all()  # ranking is the score only
    flagged = df.copy()
    top = flagged["p"].idxmax()
    base = run_select(flagged)["longs"].set_index("company_id").loc[flagged.loc[top, "company_id"], "conviction"]
    flagged.at[top, "flags"] = ["high_leverage_p90"]
    down = run_select(flagged)["longs"].set_index("company_id").loc[flagged.loc[top, "company_id"], "conviction"]
    assert base == "High" and down == "Medium"


def test_group_cap_and_list_sizes() -> None:
    sel = run_select(make_pred(600), top_n=25, max_per_group=2, avoid_n=15)
    assert len(sel["longs"]) == 24 and sel["longs"]["industry_group"].value_counts().max() == 2  # 12 groups x 2
    assert len(sel["avoid"]) == 15 and (sel["avoid"]["decile"] == 1).all()


def test_hard_filters_drop_illiquid_small_cheap_and_incomplete_names() -> None:
    df = make_pred()
    hi = df["p"].nlargest(4).index
    df.loc[hi[0], "adv20_cr"] = 0.5
    df.loc[hi[1], "mcap_pit_cr"] = 100.0
    df.loc[hi[2], "last_close"] = 10.0
    df.loc[hi[3], "feat_complete_share"] = 0.3
    ids = set(run_select(df)["longs"]["company_id"])
    assert not (set(df.loc[hi, "company_id"]) & ids)


# ── overlay hook: Q unchanged, exclude removes, boost one notch ────────────────────────────────────────────────
class FakeOverlay:
    name = "fake"

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def effects(self, as_of: date, company_ids) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=list(overlay.EFFECT_COLUMNS))


def eff_row(cid: int, action: str) -> dict:
    return {"company_id": cid, "action": action, "person": "p", "direction": "bullish", "subject": "s", "thesis": "t",
            "url": "u", "posted_at": "2026-10-01", "age_days": 10, "weight": 1.0}  # fmt: skip


def test_overlay_exclude_boost_and_q_unchanged() -> None:
    df = make_pred(1000)
    df.at[df["p"].nlargest(3).index[2], "flags"] = ["high_leverage_p90"]  # High -> Medium; a boost brings it back
    plain = run_select(df)["longs"]
    target_hi = int(plain.iloc[0]["company_id"])
    mid = plain[plain["conviction"] == "Medium"].iloc[0]
    ov = FakeOverlay([eff_row(target_hi, "exclude"), eff_row(int(mid["company_id"]), "boost")])
    eff = ov.effects(AS_OF, list(df["company_id"]))
    out = select.select_ideas(df, "3M", select.IdeasConfig(), AS_OF, REGIME, eff)
    assert target_hi not in set(out["longs"]["company_id"]) and target_hi in set(out["excluded"]["company_id"])
    row = out["longs"].set_index("company_id").loc[int(mid["company_id"])]
    assert row["conviction"] == "High"  # one notch up
    # Q (alpha / band) and the score are exactly the stored model values: the overlay never touches them
    src = df.set_index("company_id").loc[int(mid["company_id"])]
    assert (row["alpha"], row["lo"], row["hi"], row["p"]) == (src["alpha"], src["lo"], src["hi"], src["p"])
    # the rank order of the remaining names is unchanged
    assert list(out["longs"]["company_id"][:5]) == [c for c in plain["company_id"] if c != target_hi][:5]


def test_overlay_validation_rejects_unknown_actions_and_null_overlay_is_empty() -> None:
    assert overlay.NullOverlay().effects(AS_OF, [1, 2]).empty
    bad = pd.DataFrame([{**eff_row(1, "teleport")}])
    with pytest.raises(ValueError, match="unknown actions"):
        overlay.validate(bad)


def test_challenger_columns_never_change_selection() -> None:
    df = make_pred(500)
    base = run_select(df)["longs"]["company_id"].tolist()
    with_chal = df.copy()
    rng = np.random.default_rng(1)
    with_chal["challenger"] = [
        {"is": float(x), "neutral_p_bvls": float(x) / 100} for x in rng.integers(0, 100, len(df))
    ]
    assert run_select(with_chal)["longs"]["company_id"].tolist() == base
    sel = run_select(with_chal)
    for k in ("longs", "avoid"):
        sel[k] = sel[k].assign(continuity="new")
    md = render.render_horizon("3M", AS_OF, "T", sel, None, REGIME, {"anchors": [1], "n_rows": 1}, None, 1)
    assert "research challenger, unpromoted" in md and "not investment advice" in md and "(live)" in md


# ── continuity ──────────────────────────────────────────────────────────────────────────────────────────────
def test_continuity_new_continuing_dropped() -> None:
    cur = pd.DataFrame({"company_id": [1, 2, 3], "side": ["long", "long", "avoid"], "symbol": list("abc")})
    prev = pd.DataFrame({"idea_id": [10, 11, 12], "company_id": [1, 9, 3], "symbol": list("axc"),
                         "side": ["long", "long", "long"], "rank": [1, 2, 3], "as_of": date(2026, 10, 1)})  # fmt: skip
    lab, pid, dropped = select.continuity(cur, prev)
    assert lab.tolist() == ["continuing", "new", "new"]  # company 3 was a long, now an avoid: treated as new
    assert pid.iloc[0] == 10 and pd.isna(pid.iloc[1])
    assert set(dropped["company_id"]) == {9, 3}
    lab0, _, d0 = select.continuity(cur, None)
    assert set(lab0) == {"new"} and d0.empty


# ── idempotency keys ────────────────────────────────────────────────────────────────────────────────────────
def test_run_key_is_deterministic_and_sensitive_to_the_consumed_predict_run() -> None:
    a = compute_run_key("ideas", as_of=AS_OF, snap_id="s", config_hash="h", params={"predict_run": 1, "llm": False})
    assert a == compute_run_key(
        "ideas", as_of=AS_OF, snap_id="s", config_hash="h", params={"llm": False, "predict_run": 1}
    )
    assert a != compute_run_key(
        "ideas", as_of=AS_OF, snap_id="s", config_hash="h", params={"predict_run": 2, "llm": False}
    )


# ── explanations and neutral ranks ──────────────────────────────────────────────────────────────────────────
def test_contributions_sum_to_the_score_exactly() -> None:
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(50, 4)), columns=list("abcd"))
    w = {"a": 0.25, "b": -0.25, "c": 0.5, "d": -0.1}
    m = FixedWeights(w).fit(X, np.zeros(len(X)), pd.DataFrame())
    s = m.predict(X, pd.DataFrame())
    sc = score.Scored("M", "1Y", pd.DataFrame({"score": s}), X, w)
    c = score.contributions(sc)
    assert np.allclose(c.sum(axis=1), s, atol=1e-12)
    assert score.contributions(score.Scored("M", "1Y", sc.frame, X, None)) is None


def test_neutral_rank_removes_a_pure_beta_score() -> None:
    rng = np.random.default_rng(0)
    n = 2000
    beta = rng.uniform(0.3, 1.8, n)
    alpha = rng.normal(size=n)
    s = pd.Series(-2.0 * beta + 0.5 * alpha + rng.normal(0, 0.01, n))
    p = score.percentile(s)
    ctrl = pd.DataFrame({"beta_252": beta, "rvol_63": rng.uniform(0.1, 0.6, n), "idio_vol": rng.uniform(0.1, 0.5, n),
                         "mcap_pit_cr": 1000.0 + rng.uniform(0, 5000, n), "adv63_lakh": 500.0 + rng.uniform(0, 900, n)})  # fmt: skip
    nr = score.neutral_ranks(p, ctrl)
    assert abs(np.corrcoef(p, beta)[0, 1]) > 0.7
    assert abs(np.corrcoef(nr["neutral_p_bvls"], beta)[0, 1]) < 0.05
    assert np.corrcoef(nr["neutral_p_bvls"], alpha)[0, 1] > 0.9


def test_ood_guard_flags_a_planted_outlier_and_the_threshold_is_p99_of_training() -> None:
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(3000, 6)), columns=[f"f{i}" for i in range(6)])
    g = score.fit_ood(X, [pd.Timestamp("2024-06-28")], n_jobs=1)
    _s_train, f_train = g.flags(X)
    assert abs(f_train.mean() - 0.01) < 0.002
    out = X.iloc[:5].copy() + 25.0
    assert g.flags(out)[1].all()
    again = score.fit_ood(X, [pd.Timestamp("2024-06-28")], n_jobs=1)
    assert again.threshold == g.threshold  # seeded


# ── LLM summary: no network, key from env only ──────────────────────────────────────────────────────────────
def test_llm_summary_needs_an_env_key_and_never_leaks_it() -> None:
    cfg = llm_summary.LlmConfig(max_names=2)
    with pytest.raises(llm_summary.LlmUnavailable, match="OPENAI_API_KEY"):
        llm_summary.summarise([{"company_id": 1, "symbol": "X", "latest": "t", "prior": None}], cfg, environ={})
    secret = "sk-test-SECRET-123"
    calls = []

    def fake(body, key, timeout):
        calls.append((body, key))
        return {"choices": [{"message": {"content": '{"guidance": ["g"], "risks": ["r"], "changes_vs_prior": []}'}}]}

    items = [{"company_id": i, "symbol": f"S{i}", "latest": "x" * 10_000, "prior": "y"} for i in range(5)]
    out = llm_summary.summarise(items, cfg, transport=fake, environ={"OPENAI_API_KEY": secret, "OPENAI_MODEL": "m1"})
    assert len(out) == 2 and len(calls) == 2  # max_names
    assert llm_summary.LABEL == render.LLM_LABEL
    assert out[0]["label"] == "live, unvalidated, LLM-generated" and out[0]["model"] == "m1"
    assert len(calls[0][0]["messages"][1]["content"]) < 7000  # excerpt cap
    assert secret not in str(out)

    def boom(body, key, timeout):
        raise llm_summary.LlmUnavailable("OpenAI HTTP 401")

    err = llm_summary.summarise(items[:1], cfg, transport=boom, environ={"OPENAI_API_KEY": secret})
    assert secret not in str(err) and "error" in err[0]


def test_cli_registers_predict_and_ideas_subapps() -> None:
    from typer.testing import CliRunner

    from ideation.cli import app

    runner = CliRunner()
    for cmd, opt in (("predict", "--no-ens"), ("ideas", "--llm-summary")):
        res = runner.invoke(app, [cmd, "--help"])
        assert res.exit_code == 0 and opt in res.output and "--horizon" in res.output
