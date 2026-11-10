"""MS1 statistics: name-bootstrap IC CI, per-anchor table, nested calibration history, CPCV/PBO inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.bakeoff import metrics as M
from ideation.cv.cpcv import cpcv_splits
from ideation.eval.pbo import pbo_from_is_oos
from ideation.rank.percentile import percentile


def oos_frame(n_anchors: int = 6, n: int = 400, rho: float = 0.2, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_anchors):
        label = rng.normal(size=n)
        score = rho * label + rng.normal(size=n)
        ret = 0.3 * label + rng.normal(scale=0.5, size=n)
        rows.append(pd.DataFrame({"anchor": pd.Timestamp(2019 + k, 6, 28), "security_id": np.arange(n), "score": score,
                                  "label": label, "ret_total": ret, "excess_univ_med": ret - np.median(ret),
                                  "excess_n500_pr": ret - 0.05, "labelled": True,
                                  "label_end": pd.Timestamp(2020 + k, 6, 28)}))  # fmt: skip
    f = pd.concat(rows, ignore_index=True)
    f["p"] = percentile(f["score"], by=f["anchor"].to_numpy()).to_numpy()
    return f


def test_boot_ic_ci_brackets_the_estimate_and_is_reproducible() -> None:
    f = oos_frame()
    f = f[f["anchor"] == f["anchor"].min()]
    ic = f["score"].corr(f["label"], method="spearman")
    lo, hi = M.boot_ic_over_names(f["score"].to_numpy(), f["label"].to_numpy(), n_boot=2000)
    assert lo < ic < hi and hi - lo < 0.2
    assert (lo, hi) == M.boot_ic_over_names(f["score"].to_numpy(), f["label"].to_numpy(), n_boot=2000)


def test_per_anchor_columns_and_turnover_rule() -> None:
    pa = M.per_anchor(oos_frame(), n_boot=200)
    assert len(pa) == 6 and (pa["ic"] > 0).all()
    assert pa["turnover_q5"].iloc[0] == 1.0  # first anchor: full turnover assumed
    assert (pa["turnover_q5"].iloc[1:] > 0.5).all()  # independent draws each anchor
    assert (pa["spread_q5_q1_cost_adj"] < pa["spread_q5_q1"]).all()
    assert (pa["coverage"] == 1.0).all()


def test_nested_calibration_has_no_history_at_the_first_counted_anchor() -> None:
    f = oos_frame()
    counted = sorted(f["anchor"].unique())[1:]  # the first anchor is "indicative"
    nested = M.nested_calibration(f, counted, "nested")
    assert nested[counted[0]]["cal"] is None
    assert nested[counted[1]]["history"] == (counted[0],)
    last = nested[counted[-1]]
    assert set(last["history"]) == set(counted[:-1]) and 0.0 <= last["coverage"] <= 1.0
    pooled = M.nested_calibration(f, counted, "pooled")
    assert set(pooled[counted[0]]["history"]) == set(counted)


def test_pbo_inputs_use_out_of_sample_anchor_ics_only() -> None:
    anchors = pd.to_datetime([f"{y}-06-28" for y in range(2016, 2025)])
    ends = {a: a + pd.DateOffset(years=1) for a in anchors}
    splits = cpcv_splits(list(anchors), ends, 9, 2, 1)
    mat = pd.DataFrame(np.random.default_rng(0).normal(size=(9, 7)), index=anchors)
    is_p, oos_p = M.pbo_inputs(mat, splits)
    assert is_p.shape == oos_p.shape == (36, 7)
    np.testing.assert_allclose(oos_p[0], mat.loc[list(splits[0].test_anchors)].mean().to_numpy())
    res = pbo_from_is_oos(is_p, oos_p)
    assert 0.0 <= res.pbo <= 1.0
