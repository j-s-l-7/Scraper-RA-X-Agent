"""PIT data contract (02 §1.1). Phase 0 defines the types; ``PitStore``/``PitView`` are built in Phase 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

Horizon = Literal["1Y", "3M", "1M"]


class LeakageError(AssertionError):
    """A frame handed to a feature builder contains a row with ``available_from > anchor``."""


@dataclass(frozen=True)
class Anchor:
    date: date
    """NSE session; the decision is taken after its close."""
    horizon: Horizon


class PitStore:
    """Local parquet snapshots with ``available_from`` on every row; the only entry point for features."""

    def __init__(self, snapshot_dir: Path, mode: Literal["backtest", "live"]) -> None:
        raise NotImplementedError("PitStore is built in Phase 1 (02 §1.1, 03 §E P1)")


class PitView:
    """Immutable per-anchor view; every accessor returns frames with ``max(available_from) <= anchor``."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("PitView is built in Phase 1 (02 §1.1, 03 §E P1)")
