"""Typed view of ``config/experts.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ExtractionCfg:
    model: str = "gpt-5.5"
    prompt_version: str = "v1"
    max_posts: int = 2000
    since_months: int = 12
    timeout_s: int = 120
    max_retries: int = 2
    max_text_chars: int = 4000
    request_extra: dict[str, Any] = field(default_factory=dict)
    price_in_per_mtok: float = 5.0
    price_out_per_mtok: float = 20.0
    prompt_overhead_tokens: int = 650
    est_out_tokens_per_post: int = 250


@dataclass(frozen=True)
class OverlayCfg:
    window_days: int = 90
    boost_min_confidence: str = "medium"
    exclude_directions: tuple[str, ...] = ("bearish", "avoid")
    boost_directions: tuple[str, ...] = ("bullish",)


@dataclass(frozen=True)
class LedgerCfg:
    horizons_months: tuple[int, ...] = (1, 3, 12)
    min_matured: int = 30
    benchmark_code: str = "^NSEI"


@dataclass(frozen=True)
class ExpertsConfig:
    label: str
    ledger_start: date
    authors: dict[str, str]  # handle -> role
    extraction: ExtractionCfg
    overlay: OverlayCfg
    ledger: LedgerCfg

    def with_role(self, *roles: str) -> list[str]:
        return [h for h, r in self.authors.items() if r in roles]

    @classmethod
    def from_methodology(cls, data: dict[str, Any]) -> ExpertsConfig:
        d = data["experts"]["experts"] if "experts" in data.get("experts", {}) else data["experts"]
        ov = d.get("overlay", {})
        led = d.get("ledger", {})
        ex = d.get("extraction", {})
        ls = d["ledger_start"]
        return cls(
            label=d["label"],
            ledger_start=ls if isinstance(ls, date) else date.fromisoformat(str(ls)),
            authors={h: str(v["role"]) for h, v in d["authors"].items()},
            extraction=ExtractionCfg(**{k: ex[k] for k in ExtractionCfg.__dataclass_fields__ if k in ex}),
            overlay=OverlayCfg(
                window_days=int(ov.get("window_days", 90)),
                boost_min_confidence=str(ov.get("boost_min_confidence", "medium")),
                exclude_directions=tuple(ov.get("exclude_directions", ("bearish", "avoid"))),
                boost_directions=tuple(ov.get("boost_directions", ("bullish",))),
            ),
            ledger=LedgerCfg(
                horizons_months=tuple(led.get("horizons_months", (1, 3, 12))),
                min_matured=int(led.get("min_matured", 30)),
                benchmark_code=str(led.get("benchmark_code", "^NSEI")),
            ),
        )
