# ruff: noqa: E501
"""The lab checkpoint key must change when a feature column, a label or the code version changes (stale-fit hazard)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.lab import run as R


class Fake(R.LabRun):
    def __init__(self) -> None:  # no disk access
        rng = np.random.default_rng(0)
        self.master = pd.DataFrame({"a": rng.normal(size=50), "b": rng.normal(size=50)})
        fr = pd.DataFrame({"anchor": pd.Timestamp("2020-06-30"), "security_id": range(50), "ret_total": rng.normal(size=50),
                           "label_end": pd.Timestamp("2021-06-30")})  # fmt: skip

        class P:
            frame = fr

        self.panel = P()  # type: ignore[assignment]


def test_key_changes_with_feature_label_and_code(monkeypatch) -> None:
    f = Fake()
    k0 = f.data_key(["a"])
    assert f.data_key(["a"]) == k0
    assert f.data_key(["b"]) != k0
    f.master.loc[3, "a"] += 1e-6
    assert f.data_key(["a"]) != k0
    k1 = f.data_key(["a"])
    f.panel.frame.loc[0, "ret_total"] += 1.0
    assert f.data_key(["a"]) != k1
    k2 = f.data_key(["a"])
    monkeypatch.setattr(R, "CODE_VERSION", "lab-x")
    assert f.data_key(["a"]) != k2
