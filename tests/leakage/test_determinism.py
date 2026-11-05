"""02 T-19 scaffold (trivial pipeline): seeded data → RF fit → score parquet; SHA-256 identical across runs and
across n_jobs. Phase 4 replaces the synthetic pipeline with fold Jun-2022.

Rule this pins for later phases: fit with any n_jobs, but **predict with n_jobs=1**. sklearn's forest predict
accumulates per-tree outputs from threads in completion order, so parallel predict is not bit-reproducible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.repro import frame_sha256


def run_pipeline(seed: int, fit_jobs: int) -> pd.DataFrame:
    from sklearn.ensemble import RandomForestRegressor

    rng = np.random.default_rng(seed)
    x = rng.normal(size=(600, 8))
    y = 0.3 * x[:, 0] - 0.2 * x[:, 1] + rng.normal(scale=1.0, size=600)
    model = RandomForestRegressor(n_estimators=40, max_depth=5, random_state=seed, n_jobs=fit_jobs)
    model.fit(x[:400], y[:400])
    model.set_params(n_jobs=1)
    scores = model.predict(x[400:])
    return pd.DataFrame({"row": np.arange(400, 600), "score": scores})


def test_determinism_same_seed() -> None:
    assert frame_sha256(run_pipeline(7, 1)) == frame_sha256(run_pipeline(7, 1))


def test_determinism_across_n_jobs() -> None:
    assert frame_sha256(run_pipeline(7, 1)) == frame_sha256(run_pipeline(7, -1))


def test_hash_detects_a_change() -> None:
    a = run_pipeline(7, 1)
    b = a.copy()
    b.loc[0, "score"] = np.nextafter(b.loc[0, "score"], np.inf)
    assert frame_sha256(a) != frame_sha256(b)
    assert frame_sha256(run_pipeline(7, 1)) != frame_sha256(run_pipeline(8, 1))
