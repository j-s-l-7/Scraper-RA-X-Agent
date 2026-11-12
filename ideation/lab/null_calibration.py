# ruff: noqa: E501
"""Null calibration of the shuffled-target control (diagnostic, NOT a new trial).

CTRL_SHUFFLED is one draw of a random model. Its IC is not zero in a block: one fitted random direction is applied to 12
persistent monthly cross-sections, so the noise sits at the model level (about one independent draw per fold), which a
bootstrap over months cannot see. This runs the registered CTRL_SHUFFLED config with ``seed`` 0..K-1 to get the real
null distribution of the mean IC (monthly design and June design). Same config otherwise; N is not changed."""

from __future__ import annotations

import json
from collections.abc import Callable

import numpy as np

from ideation.lab import engine as E
from ideation.lab.run import LabRun
from ideation.lab.stats import ic_by_anchor
from ideation.trials.registry import load_family


def run_null(lr: LabRun, k: int = 30, log: Callable[[str], None] = print) -> dict[str, dict[str, float]]:
    spec = next(s for s in load_family("1Y-monthly-v2") if s.trial_id == "CTRL_SHUFFLED")
    fr = lr.panel.frame
    out: dict[str, list[float]] = {"monthly": [], "june": []}
    for seed in range(k):
        cfg = {**spec.config, "seed": seed}
        for d in ("monthly", "june"):
            sc = E.run_learner(lr.panel, lr.master, cfg, lr.folds[d], design=d)
            x = sc.merge(fr[["anchor", "security_id", "ret_total", "labelled"]], on=["anchor", "security_id"])
            ic = ic_by_anchor(x[x["labelled"]]).dropna()
            if d == "june":
                ic = ic[[a.month == 6 for a in ic.index]]
            out[d].append(float(ic.mean()))
        log(f"null seed {seed}")
    res = {d: {"mean": float(np.mean(v)), "sd": float(np.std(v, ddof=1)), "p05": float(np.quantile(v, 0.05)), "p95": float(np.quantile(v, 0.95)),
               "max_abs": float(np.max(np.abs(v))), "n": len(v)} for d, v in out.items()}
    return res


if __name__ == "__main__":
    from pathlib import Path

    from ideation.lab.build_fund import lab_dir

    d = Path.home() / "Ideation-data"
    snap = sorted(p.name for p in (d / "outputs").iterdir() if (p / "features_fundamental.parquet").exists())[-1]
    lr = LabRun(d, snap, log=lambda m: None)
    r = run_null(lr)
    (lab_dir(d, snap) / "null_calibration.json").write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))
