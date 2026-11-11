# ruff: noqa: E501, N805
"""MS3: template has no literal numbers, the report refuses without its mandatory elements, consensus math, unseal guard."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ideation.bakeoff.driver import control_spec
from ideation.eval import consensus as CS
from ideation.report import portfolio as PF
from ideation.report.holdout import UnsealRefused, unseal
from ideation.report.research import (
    Est,
    FamilyResult,
    ReportRefused,
    render,
    render_mechanism,
    validate,
)
from ideation.trials.registry import load_family

ROOT = Path(__file__).resolve().parents[2]
BIAS = "DATA & BIAS  (run 1)\nHoldout: sealed 1Y 2025-03-01..; unsealed no"


def _fam(**kw):
    base = {"family": "1Y-june", "n_trials": 50, "md": "x", "headlines": [Est("ic", 0.1, 0.0, 0.2, 90, 5)], "bias_block": BIAS}
    base.update(kw)
    return FamilyResult(**base)


def test_template_has_no_literal_numbers():
    t = (ROOT / "docs" / "RANKING_MECHANISM.template.md").read_text()
    t = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", "", t, flags=re.S)
    bad = [ln for ln in t.splitlines() if re.search(r"\d", ln) and not re.match(r"^#{1,3} \d+\.", ln)]
    assert bad == [], bad


def _mech_ctx(**kw):
    ctx = {"as_of": "d", "features": [], "zoo": [], "formulas": __import__("collections").defaultdict(str), "bucket_rows": ["a"],
           "families": [{"family": "f", "n": 3, "label": "l", "champion": "c", "baseline": "b", "tables": {}, "bias_block": BIAS}],
           "holdout": "sealed", "unsealed": False}  # fmt: skip
    ctx.update(kw)
    return ctx


def test_mechanism_renders_and_refuses():
    out = render_mechanism(_mech_ctx())
    assert "DATA & BIAS" in out
    with pytest.raises(ReportRefused):
        render_mechanism(_mech_ctx(families=[{"family": "f", "n": 3, "label": "l", "champion": "c", "baseline": "b", "tables": {}, "bias_block": ""}]))
    with pytest.raises(ReportRefused):
        render_mechanism(_mech_ctx(holdout=""))
    f = _mech_ctx()["families"][0] | {"n": 0}
    with pytest.raises(ReportRefused):
        render_mechanism(_mech_ctx(families=[f]))


def test_report_refuses_without_bias_n_ci_or_holdout():
    validate([_fam()], {})  # sealed (empty dict) is a valid status
    with pytest.raises(ReportRefused, match="bias block"):
        validate([_fam(bias_block="")], {})
    with pytest.raises(ReportRefused, match="N = 49"):
        validate([_fam(n_trials=49)], {})
    with pytest.raises(ReportRefused, match="not an estimate"):
        validate([_fam(headlines=[0.12])], {})  # a bare float
    with pytest.raises(ReportRefused, match="valid CI"):
        validate([_fam(headlines=[Est("ic", 0.1, float("nan"), 0.2, 90, 5)])], {})
    with pytest.raises(ReportRefused, match="status"):
        validate([_fam()], None)
    with pytest.raises(ReportRefused, match="no headline"):
        validate([_fam(headlines=[])], {})
    with pytest.raises(ReportRefused):
        render([_fam(bias_block="")], {}, "d")


def test_consensus_math_on_planted_signal():
    rng = np.random.default_rng(0)
    rets, mem = [], {f"m{i}": [] for i in range(10)}
    for a in pd.date_range("2020-06-30", periods=4, freq="365D"):
        n = 400
        sig = rng.normal(size=n)
        r = 0.05 * sig + rng.normal(scale=0.2, size=n)
        rets.append(pd.DataFrame({"anchor": a, "security_id": np.arange(n), "ret_total": r, "excess_univ_med": r - np.median(r),
                                  "excess_n500_pr": r - 0.1, "excess_univ_cw": r}))  # fmt: skip
        for m in mem:
            s = sig + rng.normal(scale=0.7, size=n)
            p = (pd.Series(s).rank() - 0.5) / n
            mem[m].append(pd.DataFrame({"anchor": a, "security_id": np.arange(n), "p": p.to_numpy()}))
    members = {k: pd.concat(v) for k, v in mem.items()}
    res = CS.evaluate(members, pd.concat(rets), mean_block=1.0, n_boot=500, n_draws=100)
    s = res.sets.set_index("set")
    assert s.loc["C_maj", "mean n"] > 0
    assert s.loc["C_maj", "ex_med"] > s.loc["B_maj", "ex_med"]  # the planted signal is recovered
    assert res.best_single in members
    assert res.agreement["kendall_w_mean"] > 0.3
    assert {"C_maj", "B_maj"} <= set(res.paired["a"]) | set(res.paired["b"])


def test_capped_cw_and_costs():
    w = PF.capped_cw(pd.Series([100.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]), 0.2)
    assert abs(w.sum() - 1) < 1e-9 and w.max() <= 0.2 + 1e-9
    c = PF.load_costs()
    cost, turn = PF.rebalance_cost(None, pd.Series([0.5, 0.5], index=[1, 2]), c)
    assert turn == pytest.approx(1.0) and 0 < cost < 0.01
    cost2, turn2 = PF.rebalance_cost(pd.Series([0.5, 0.5], index=[1, 2]), pd.Series([0.5, 0.5], index=[1, 2]), c)
    assert turn2 == 0 and cost2 == 0


def test_control_arm_is_not_a_trial_and_has_own_hash():
    specs = load_family("1Y-june")
    t = next(s for s in specs if s.trial_id == "RF_GLOBAL_TEXT")
    c = control_spec(t)
    assert c.config["features"] == "core" and c.config["subset"] == "text_covered"
    assert c.config_hash != t.config_hash and c.trial_id.endswith("__CTRL")


class _Conn:
    def __init__(self, unsealed=(), trials=None):
        self.unsealed, self.trials, self.inserted = set(unsealed), trials or {}, []

    def execute(self, q, p=None):
        outer = self

        class R:
            def fetchone(self_inner):
                if "from ideation.holdout_unseal" in q:
                    return (1,) if p[0] in outer.unsealed else None
                if "from ideation.experiment_trial" in q:
                    return outer.trials.get(p[0])
                return None

        if q.strip().startswith("insert"):
            self.inserted.append(p)
        return R()


def test_unseal_guards():
    ok = {7: ("1Y-june", "abc", "ok"), 8: ("1Y-june", "def", "ok"), 9: ("tactical-3M", "zzz", "ok")}
    with pytest.raises(UnsealRefused, match="--yes"):
        unseal(_Conn(trials=ok), "1Y-june", 7, 8, yes=False)
    with pytest.raises(UnsealRefused, match="already"):
        unseal(_Conn(unsealed={"1Y-june"}, trials=ok), "1Y-june", 7, 8, yes=True)
    with pytest.raises(UnsealRefused, match="not a trial"):
        unseal(_Conn(trials=ok), "1Y-june", 9, 8, yes=True)
    with pytest.raises(UnsealRefused, match="unknown family"):
        unseal(_Conn(trials=ok), "nope", 7, 8, yes=True)
    c = _Conn(trials=ok)
    assert unseal(c, "1Y-june", 7, 8, yes=True)["champion"] == "abc" and len(c.inserted) == 1
