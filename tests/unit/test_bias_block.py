"""Bias block (02 §8.2 + 05 §8 lines) renders every mandatory line from facts, and imports no lake/psycopg (E1's
eval import rule)."""

from __future__ import annotations

import ast
from datetime import date

import pytest

from ideation.config import REPO_ROOT
from ideation.eval import bias_block
from ideation.eval.bias_block import BiasBlockError, gather, render_from_facts

EXITS = {"stopped": 1052, "true_exit": 665, "last_bz": 204, "last_be": 309}
BIAS = {
    "june_1y_anchors": ["2016-06-30", "2024-06-28"],
    "covered_minus_uncovered_median_1y": {"2016-06-30": 0.239, "2019-06-28": -0.049},
    "distress_1y": 57, "corp_exit_1y": 255, "stale_1y": 122, "rights_unadjusted_1y": 169,
    "no_div_data_share_1y": 0.29, "u_score_no_div_data_share_1y": 0.07, "jump_factors_applied": 231,
    "univ_median_1y_minus100": {"2019-06-28": -0.204}, "univ_median_1y_lastprice": {"2019-06-28": -0.203},
    "exits_02": EXITS,
}  # fmt: skip
COV = [
    (date(2016, 6, 30), 845, 717, 971, 1000, 0.8, {"mcap_src_u_score": {"B1": 789}, "mcap_flag_u_score": 25}),
    (date(2016, 3, 31), 800, 700, 950, 1000, 0.8,
     {"coverage_02": {"anchor": "2016-03-31", "delta_pp_02map": {"top500": 0.0, "top1000": 0.2}}}),
]  # fmt: skip
FACTS = {
    "run_id": 7, "git_sha": "abcdef123456", "snap_id": "20261105-x", "content_sha": "ff",
    "lake_max_bhav": date(2026, 11, 5), "lake_fact_ts": "2026-11-06", "params": {}, "bias": BIAS, "coverage": COV,
    "unsealed": [],
}  # fmt: skip
MANDATORY = (
    "DATA & BIAS", "Anchors:", "Universe: U_score", "U1000 slice", "Coverage bias:", "Survivorship:", "Restatement:",
    "Sector labels:", "Availability:", "Labels:", "Benchmark:", "Macro:", "Text/events:", "Holdout: sealed",
    "Live-only",
)  # fmt: skip


def test_renders_every_mandatory_line() -> None:
    text = render_from_facts(FACTS)
    for key in MANDATORY:
        assert key in text, key
    assert "2016-06 845" in text
    assert "distress -100% (57)" in text
    assert "unsealed no" in text
    assert "n/a (P4)" in text  # trial N / DSR are filled by P4
    assert "None" not in text


def test_missing_inputs_fail_loudly() -> None:
    class Conn:
        def execute(self, sql: str, params: object = None) -> Conn:
            self._sql = sql
            return self

        def fetchone(self) -> None:
            return None

    with pytest.raises(BiasBlockError):
        gather(Conn(), 1)


def test_bias_block_imports_no_lake_or_psycopg() -> None:
    tree = ast.parse((REPO_ROOT / "ideation" / "eval" / "bias_block.py").read_text())
    mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in mods if m.startswith(("ideation.lake", "psycopg"))]
    assert "02 §8.2" in (bias_block.__doc__ or "")
