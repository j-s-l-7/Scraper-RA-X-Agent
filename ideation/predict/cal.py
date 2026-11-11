# ruff: noqa: E501
"""Live calibration (R5) and conformal band (R6) from the stored OOS predictions of the shipped model.

The history is every **counted, unsealed** OOS anchor of the trial (``rank.calibrate.fit`` with ``counted=`` and
``sealed=``; for a live anchor later than all of them the nested rule "anchors < T" selects them all). Units are
**excess return vs the universe median** over the horizon; the market leg π belongs to the portfolio phase (P7).
The research table ``ideation.rank_calibration`` is not written here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from ideation.labels.loader import is_sealed
from ideation.rank import calibrate, conformal
from ideation.rank.buckets import decile

HORIZON_CODE = {"1Y": "1Y", "3M": "3M"}


class CalibrationError(RuntimeError):
    """The stored OOS history is unusable (empty, or it would touch a sealed anchor)."""


def counted_anchors(oos_anchors: Iterable[Any], cfg: dict[str, Any], horizon: str) -> list[pd.Timestamp]:
    """Counted test anchors of a trial config (1Y: counted calendar years; 3M: ``counted_oos`` range)."""
    a = sorted({pd.Timestamp(x) for x in oos_anchors})
    if horizon == "1Y":
        years = {int(y) for y in cfg["counted_tests"]}
        return [x for x in a if x.year in years]
    lo, hi = pd.Timestamp(cfg["counted_oos"]["start"]), pd.Timestamp(cfg["counted_oos"]["end"])
    return [x for x in a if lo <= x <= hi]


@dataclass(frozen=True)
class LiveCalibration:
    horizon: str
    anchors: tuple[pd.Timestamp, ...]
    cal: calibrate.Calibration
    bands: pd.DataFrame
    hit_rate: pd.Series
    """Per decile: share of OOS names that beat the universe median (history)."""
    n_rows: int

    def apply(self, p: pd.Series) -> pd.DataFrame:
        """alpha (expected excess), 80% band [lo, hi] on excess, decile and its realized hit rate, per name."""
        a = self.cal.alpha(p.to_numpy(dtype=float))
        lo, hi = conformal.band(p, a, self.bands)
        d = decile(p)
        return pd.DataFrame({"alpha": a, "lo": lo, "hi": hi, "decile": d.to_numpy(),
                             "hit_rate": self.hit_rate.reindex(d.to_numpy()).to_numpy()}, index=p.index)  # fmt: skip

    def describe(self) -> dict[str, Any]:
        t = self.cal.table
        return {
            "anchors": [str(a.date()) for a in self.anchors], "n_rows": self.n_rows,
            "alpha_iso_by_decile": {int(k): (None if pd.isna(v) else round(float(v), 5)) for k, v in t["alpha_iso"].items()},
            "band_lo_by_decile": {int(k): round(float(v), 5) for k, v in self.bands["lo"].items()},
            "band_hi_by_decile": {int(k): round(float(v), 5) for k, v in self.bands["hi"].items()},
            "hit_rate_by_decile": {int(k): round(float(v), 4) for k, v in self.hit_rate.items() if pd.notna(v)},
        }  # fmt: skip


def fit_live(oos: pd.DataFrame, counted: list[pd.Timestamp], horizon: str, live_anchor: date) -> LiveCalibration:
    """``oos``: ``anchor``, ``p``, ``excess_univ_med`` (the stored OOS rows of one trial)."""
    if any(is_sealed(a.date(), horizon) for a in counted):
        raise CalibrationError("a sealed anchor is among the counted calibration anchors")
    base = oos.dropna(subset=["p", "excess_univ_med"]).assign(excess=lambda d: d["excess_univ_med"])
    base = base.assign(anchor=pd.to_datetime(base["anchor"]))
    cal = calibrate.fit(base, pd.Timestamp(live_anchor), counted=counted, sealed=(), scheme="nested")
    if cal is None:
        raise CalibrationError("no counted OOS history to calibrate on")
    h = base[base["anchor"].isin(cal.anchors)].copy()
    h["resid"] = h["excess"] - cal.alpha(h["p"])
    bands = conformal.fit_bands(h[["anchor", "p", "resid"]])
    d = decile(h["p"])
    hit = (h["excess"] > 0).groupby(d.to_numpy()).mean().reindex(range(1, 11))
    return LiveCalibration(horizon, tuple(cal.anchors), cal, bands, hit, len(h))
