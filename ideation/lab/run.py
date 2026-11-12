# ruff: noqa: E501
# ruff: noqa: B007
"""Run the 1Y-monthly-v2 trials (and the fixed-weight benchmarks) in both designs; checkpointed per (trial, design)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from ideation.config import REPO_ROOT
from ideation.lab import design as D
from ideation.lab import engine as E
from ideation.lab.build_fund import lab_dir
from ideation.lab.panel import LabPanel, load_lab_panel
from ideation.trials.registry import load_family

FIXED_BENCH = ["P1_UNIFIED_XS", "VALUE_ONLY", "P1_COMP_EW", "MOM_12_1", "LOWVOL", "QUALITY_ONLY"]
CODE_VERSION = "lab-2"
TACTICAL_SCR = "tactical_ms2/scores/03_SCR_T0.parquet"


def load_tactical_scores(data_dir: Path) -> pd.DataFrame:
    t = pd.read_parquet(data_dir / TACTICAL_SCR, columns=["anchor", "security_id", "score"])
    t["anchor"] = pd.to_datetime(t["anchor"]).dt.normalize()
    return t


class LabRun:
    def __init__(self, data_dir: Path, snap_id: str, log: Callable[[str], None] = print) -> None:
        from ideation.features.registry import load_registry

        self.data_dir, self.snap, self.log = data_dir, snap_id, log
        self.reg = load_registry(REPO_ROOT / "config")
        self.panel: LabPanel = load_lab_panel(data_dir, snap_id, self.reg)
        self.sets = E.feature_sets(self.reg)
        self.master = E.build_master(self.panel, E.union_names(self.sets))
        self.keys: dict[tuple[str, str], str] = {}
        self.fits = 0
        self.runs = lab_dir(data_dir, snap_id) / "runs"
        self.runs.mkdir(exist_ok=True)
        fr = self.panel.frame
        self.test_end = D.COUNTED_START.replace(year=2024, month=6, day=30)
        self.folds = {
            "monthly": D.monthly_design_folds(self.panel.anchors, self.panel.label_end, self.test_end),
            "june": D.june_design_folds(self.panel.anchors, self.panel.label_end),
        }
        for k, v in self.folds.items():
            D.assert_no_overlap_leak(v, self.panel.label_end)
        log(f"panel rows {len(fr)}, anchors {len(self.panel.anchors)}; folds "
            f"{ {k: len(v) for k, v in self.folds.items()} }")  # fmt: skip

    def data_key(self, cols: list[str] | None = None) -> str:
        """sha256 over the matrix columns a trial reads, the labels, the anchors and CODE_VERSION: part of every
        checkpoint name, so a changed feature/label/code invalidates the cache (a stale-fit hazard found in the bake-off)."""
        h = hashlib.sha256(CODE_VERSION.encode())
        fr = self.panel.frame
        for name, frame in (("X", self.master[cols] if cols else self.master), ("L", fr[["anchor", "security_id", "ret_total", "label_end"]])):
            h.update(name.encode())
            h.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
        return h.hexdigest()[:12]

    def path(self, name: str, design: str, key: str = "") -> Path:
        return self.runs / f"{name}__{design}__{key}.parquet"

    def learner_scores(self, spec, design: str, force: bool = False) -> pd.DataFrame:
        feats = self.sets[spec.config["features"]]
        key = hashlib.sha256((self.data_key(E.columns_for(self.master, feats)) + E.cfg_hash12(spec.config)).encode()).hexdigest()[:12]
        p = self.path(spec.trial_id, design, key)
        self.keys[(spec.trial_id, design)] = key
        if p.exists() and not force:
            self.log(f"[cache hit] {spec.trial_id}/{design} key {key}")
            return pd.read_parquet(p)
        self.fits += 1
        t0 = time.perf_counter()
        self.log(f"[{time.strftime('%H:%M:%S')}] {spec.trial_id} / {design}")
        sc = E.run_learner(self.panel, self.master, spec.config, self.folds[design], design=design, log=self.log)
        sc.to_parquet(p, index=False)
        self.log(f"  done {time.perf_counter() - t0:.0f}s")
        return sc

    def fixed_scores(self, name: str) -> pd.DataFrame:
        fr = self.panel.frame
        if name == "BLEND_VAL_PRICE":
            s = E.blend_val_price(fr, self.master, self.reg, load_tactical_scores(self.data_dir))
        else:
            s = E.fixed_score(name, self.master, self.reg)
        out = fr[["anchor", "security_id"]].copy()
        out["score"] = s.to_numpy()
        out["fold"] = "fixed"
        return out

    def run_all(self, designs=("monthly", "june"), only: set[str] | None = None) -> dict[str, dict[str, pd.DataFrame]]:
        specs = load_family("1Y-monthly-v2")
        res: dict[str, dict[str, pd.DataFrame]] = {}
        for s in specs:
            if only and s.trial_id not in only:
                continue
            if "learner" in s.config:
                res[s.trial_id] = {d: self.learner_scores(s, d) for d in designs}
        for s in specs:
            if only and s.trial_id not in only:
                continue
            if "consensus" in s.config:
                for d in designs:
                    members = [res[m][d] for m in s.config["members"]]
                    sc = E.rank_average(members)
                    ek = hashlib.sha256("".join(self.keys[(m, d)] for m in s.config["members"]).encode()).hexdigest()[:12]
                    self.keys[(s.trial_id, d)] = ek
                    sc.to_parquet(self.path(s.trial_id, d, ek), index=False)
                    res.setdefault(s.trial_id, {})[d] = sc
            elif s.config.get("model") in ("vmql_fixed", "blend_val_price"):
                name = "VMQL_FIXED" if s.config["model"] == "vmql_fixed" else "BLEND_VAL_PRICE"
                fx = self.fixed_scores(name)
                res[s.trial_id] = {d: fx for d in designs}
        return res
