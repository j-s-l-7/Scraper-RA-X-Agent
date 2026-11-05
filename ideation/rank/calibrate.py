"""R5 (05 §2, 01 §4.3): score percentile → expected excess return, nested and isotonic.

For test anchor T the history is the OOS predictions of **counted** anchors A < T whose labels have ended by T
(``label_end ≤ T``, 03 §C3 applied to calibration; for annual 1Y this is just A < T) and never a sealed anchor.

Per decile d: ``α_d = mean(excess)`` pooled over the history (excess = r − universe median r); shrink ``α_d ← 0.5·α_d``;
isotonic increasing in d with weights n; then piecewise-linear interpolation between decile mid-percentiles
(0.05, 0.15, …, 0.95), flat beyond the end points, gives ``α(p)``. No history ⇒ ``None`` (reported as "n/a").
The ``pooled`` scheme uses every unsealed OOS anchor and is descriptive only (it uses later years).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from ideation.rank.buckets import decile

Scheme = Literal["nested", "pooled"]
DECILE_MIDS = (np.arange(1, 11) - 0.5) / 10
SHRINK = 0.5


@dataclass(frozen=True)
class Calibration:
    scheme: Scheme
    test_anchor: pd.Timestamp | None
    anchors: tuple[pd.Timestamp, ...]
    """Anchors whose OOS rows were used."""
    table: pd.DataFrame
    """Index decile 1..10; columns n, alpha_raw, alpha_shrunk, alpha_iso."""

    def alpha(self, p: np.ndarray | pd.Series) -> np.ndarray:
        t = self.table[self.table["n"] > 0]
        return np.interp(np.asarray(p, dtype=float), DECILE_MIDS[t.index.to_numpy() - 1], t["alpha_iso"].to_numpy())


def history_anchors(
    oos: pd.DataFrame,
    test_anchor: object | None,
    *,
    counted: Iterable[object] | None = None,
    sealed: Iterable[object] = (),
    scheme: Scheme = "nested",
) -> list[pd.Timestamp]:
    a = pd.to_datetime(oos["anchor"])
    ok = ~a.isin(pd.to_datetime(list(sealed)))
    if counted is not None:
        ok &= a.isin(pd.to_datetime(list(counted)))
    if scheme == "nested":
        t = pd.Timestamp(test_anchor)
        ok &= a < t
        if "label_end" in oos:
            ok &= pd.to_datetime(oos["label_end"]) <= t
    return sorted(a[ok].unique())


def fit(
    oos: pd.DataFrame,
    test_anchor: object | None,
    *,
    counted: Iterable[object] | None = None,
    sealed: Iterable[object] = (),
    scheme: Scheme = "nested",
    excess_col: str = "excess",
) -> Calibration | None:
    """``oos`` has one row per (anchor, name): ``anchor``, ``p`` (percentile at that anchor), ``excess_col`` and
    optionally ``label_end``. Returns ``None`` when the history is empty."""
    used = history_anchors(oos, test_anchor, counted=counted, sealed=sealed, scheme=scheme)
    if not used:
        return None
    h = oos[pd.to_datetime(oos["anchor"]).isin(used)].dropna(subset=["p", excess_col])
    d = decile(h["p"])
    g = h[excess_col].groupby(d)
    table = pd.DataFrame({"n": g.size(), "alpha_raw": g.mean()}).reindex(range(1, 11))
    table["n"] = table["n"].fillna(0).astype(int)
    table["alpha_shrunk"] = SHRINK * table["alpha_raw"]
    have = table["n"] > 0
    iso = IsotonicRegression(increasing=True).fit(
        table.index[have].to_numpy(dtype=float),
        table.loc[have, "alpha_shrunk"].to_numpy(),
        sample_weight=table.loc[have, "n"].to_numpy(dtype=float),
    )
    table["alpha_iso"] = np.nan
    table.loc[have, "alpha_iso"] = iso.predict(table.index[have].to_numpy(dtype=float))
    table.index.name = "decile"
    t = pd.Timestamp(test_anchor) if test_anchor is not None else None
    return Calibration(scheme, t, tuple(used), table)
