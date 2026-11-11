"""Expert-signal overlay hook (04 §5). Interface only: the expert module is a separate later phase.

``ExpertOverlay.effects(as_of, company_ids)`` returns one row per applied effect with the columns of 04 §5.
The ideas engine consumes it live-only:

* ``exclude`` removes the name from long ideas (listed under "expert-flagged"); never changes score, rank or band;
* ``boost`` is one conviction notch up (financials stay capped at Medium, 03 §A8) and c_i x 1.15; the view Q is
  never changed; a boosted name that the model ranks in the bottom decile becomes ``conflict`` and is not promoted;
* ``annotate`` / ``sector_tag`` / ``conflict`` add text only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

import pandas as pd

EFFECT_COLUMNS = ("company_id", "action", "person", "direction", "subject", "thesis", "url", "posted_at", "age_days",
                  "weight")  # fmt: skip
ACTIONS = ("exclude", "boost", "conflict", "annotate", "sector_tag")
C_BOOST = 1.15


class ExpertOverlay(Protocol):
    def effects(self, as_of: date, company_ids: Sequence[int]) -> pd.DataFrame: ...


class NullOverlay:
    """Default: no expert module is connected (it is built in a later phase)."""

    name = "none"

    def effects(self, as_of: date, company_ids: Sequence[int]) -> pd.DataFrame:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in EFFECT_COLUMNS})


def validate(effects: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in EFFECT_COLUMNS if c not in effects.columns]
    bad = sorted(set(effects.get("action", [])) - set(ACTIONS))
    if missing or bad:
        raise ValueError(f"overlay effects invalid: missing columns {missing}, unknown actions {bad}")
    return effects
