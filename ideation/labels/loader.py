"""The only way to read labels: ``load_labels(anchors, horizon, purpose)`` with holdout sealing (05 §8).

Sealed anchors raise :class:`SealedAnchorError` unless ``ideation.holdout_unseal`` has a row for the requesting
family. Each family may hold that row at most once; it is written by ``ideation holdout unseal`` (E1).

Sealed sets are date ranges, so a snapped anchor cannot slip past the seal:

* 1Y: every anchor from 2025-03-01 on. That covers Jun-2025 and Mar-2025 (05 §8), and also any monthly 1Y anchor
  whose label overlaps the holdout year. This is slightly stricter than naming only the two anchors.
* 3M: anchors 2025-07-01 … 2026-07-31.
* 1M: anchors 2025-07-01 … 2026-09-30.

Every call is appended to :data:`AUDIT` (anchors, horizon, purpose, family). T-26 uses it, and so do the fit and
calibrate entry points added in later phases. When the unseal table cannot be read, nothing is unsealed (fail
closed).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

PURPOSES = frozenset({"fit", "calibrate", "select", "evaluate", "report", "holdout_report", "qa"})
SEALED_RANGES: dict[str, tuple[date, date]] = {
    "1Y": (date(2025, 3, 1), date(9999, 12, 31)),
    "3M": (date(2025, 7, 1), date(2026, 7, 31)),
    "1M": (date(2025, 7, 1), date(2026, 9, 30)),
}
DEFAULT_FAMILY = {"1Y": "1Y-june", "3M": "tactical-3M", "1M": "tactical-3M"}
FAMILY_HORIZONS = {"1Y-june": {"1Y"}, "1Y-march": {"1Y"}, "tactical-3M": {"3M", "1M"}, "tactical-1M": {"1M"}}


class SealedAnchorError(PermissionError):
    """A sealed holdout anchor was requested before the family was unsealed (05 §8)."""


@dataclass(frozen=True)
class LoadRecord:
    anchors: tuple[date, ...]
    horizon: str
    purpose: str
    family: str


AUDIT: list[LoadRecord] = []


def is_sealed(anchor: date, horizon: str) -> bool:
    lo, hi = SEALED_RANGES[horizon]
    return lo <= anchor <= hi


def sealed_anchors(anchors: Iterable[date], horizon: str) -> list[date]:
    return [a for a in anchors if is_sealed(a, horizon)]


def unsealed_only(anchors: Iterable[date], horizon: str) -> list[date]:
    """Drop sealed anchors: the helper every QA/report path uses before calling :func:`load_labels`."""
    return [a for a in anchors if not is_sealed(a, horizon)]


def unsealed_families_from_db() -> set[str]:
    """Families with a row in ``ideation.holdout_unseal``; empty (fail closed) when it cannot be read."""
    try:
        from ideation.config import get_settings
        from ideation.db.writer import Writer

        with Writer.from_settings(get_settings()).connect() as conn:
            exists = conn.execute("select to_regclass('ideation.holdout_unseal') is not null").fetchone()
            if not exists or not exists[0]:
                return set()
            return {r[0] for r in conn.execute("select family from ideation.holdout_unseal").fetchall()}
    except Exception:  # any failure means "nothing unsealed" (fail closed)
        return set()


def load_labels(
    anchors: Iterable[date],
    horizon: str,
    purpose: str,
    *,
    family: str | None = None,
    source: pd.DataFrame | Path | None = None,
    unsealed: Callable[[], set[str]] | set[str] | None = None,
) -> pd.DataFrame:
    """Labels for ``anchors`` × ``horizon``; raises on a sealed anchor unless ``family`` is unsealed."""
    if horizon not in SEALED_RANGES:
        raise ValueError(f"unknown horizon {horizon!r}")
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {sorted(PURPOSES)}")
    fam = family or DEFAULT_FAMILY[horizon]
    if horizon not in FAMILY_HORIZONS.get(fam, set()):
        raise ValueError(f"family {fam!r} does not cover horizon {horizon}")
    req = tuple(sorted(set(anchors)))
    AUDIT.append(LoadRecord(req, horizon, purpose, fam))
    sealed = sealed_anchors(req, horizon)
    if sealed:
        fams = unsealed() if callable(unsealed) else (unsealed if unsealed is not None else unsealed_families_from_db())
        if fam not in fams:
            raise SealedAnchorError(
                f"{len(sealed)} sealed {horizon} anchor(s) requested ({sealed[0]} …) for family {fam!r}, purpose "
                f"{purpose!r}; the holdout is sealed until `ideation holdout unseal --family {fam}` (05 §8)"
            )
    if source is None:
        raise ValueError("load_labels needs a source (labels parquet path or frame)")
    df = pd.read_parquet(source) if isinstance(source, Path) else source
    out = df[(df["horizon"] == horizon) & df["anchor"].isin(req)]
    return out.reset_index(drop=True)
