"""Feature registry (05 §10.1 rule 3, 01 §2.7): loads every ``config/features_*.yaml`` and validates it.

The YAML files are the single source of truth for column names, sign priors, VIF families, layout applicability and
the metric lines a builder reads. The registry never touches the database; :meth:`Registry.validate_metrics` checks
the ``lines:`` aliases against a metric catalog frame (``PitView.metric()`` / ``metric.parquet``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ideation.rank.transform import columns_of_kind

LAYOUTS = ("core", "fin_layout", "fin_other")
KINDS = ("cross_sectional", "broadcast")
SETS = ("core", "ext", "shown", "text", "vendor", "tech", "t1", "regime", "annual", "text_trend")
"""P2 sets: core / ext / shown. P3 sets (tactical): tech = T0 price and volume columns, t1 = short-history columns
(delivery, FPI sector flow, ownership, events), regime = broadcast regime and macro columns.
T0 = tech, T0_regime = tech + regime, T1 = tech + t1; 1Y core_mom / core_ext pick names from
tech and regime. No P3 column is in the P2 sets, so
``names(sets=['core'])`` never pulls a technical column into the 1Y core matrix."""
REQUIRED = ("family", "prior", "kind", "category", "set")  # layouts / growth / need_fy / lines have defaults


class RegistryError(ValueError):
    """A feature YAML is malformed, or refers to a metric the catalog does not resolve uniquely."""


@dataclass(frozen=True)
class Feature:
    name: str
    family: str
    prior: int | None
    kind: str
    category: str
    layouts: tuple[str, ...]
    set: str
    growth: bool
    need_fy: int
    lines: tuple[str, ...]
    complete: bool
    source: str
    trainable_from: date | None = None

    @property
    def counts_in_completeness(self) -> bool:
        return self.complete


@dataclass(frozen=True)
class Registry:
    features: Mapping[str, Feature]
    lines: Mapping[str, tuple[str, str]]
    cleaning: Mapping[str, Any]
    files: tuple[str, ...] = field(default_factory=tuple)

    def names(
        self, *, sets: Iterable[str] | None = None, layout: str | None = None, source: str | None = None
    ) -> list[str]:
        """Feature names, optionally restricted to sets, a layout, or one YAML file (a builder only owns its file)."""
        sets = set(sets) if sets is not None else None
        return [
            n for n, f in self.features.items()
            if (sets is None or f.set in sets) and (layout is None or layout in f.layouts)
            and (source is None or f.source == source)
        ]  # fmt: skip

    def trainable_from(self) -> dict[str, date]:
        """Feature → first anchor at which it may be used in a training matrix (absent = no restriction)."""
        return {n: f.trainable_from for n, f in self.features.items() if f.trainable_from is not None}

    def priors(self) -> dict[str, int]:
        """Feature → ±1 for features with a prior (the shape ``TransformSpec.priors`` / ``scr`` expect)."""
        return {n: f.prior for n, f in self.features.items() if f.prior in (1, -1)}

    def families(self) -> dict[str, str]:
        return {n: f.family for n, f in self.features.items()}

    def growth_cols(self) -> frozenset[str]:
        return frozenset(n for n, f in self.features.items() if f.growth)

    def broadcast_cols(self) -> frozenset[str]:
        return columns_of_kind({n: {"kind": f.kind} for n, f in self.features.items()}, "broadcast")

    def applicable(self, name: str, layout: str) -> bool:
        return layout in self.features[name].layouts

    def validate_metrics(self, metric: pd.DataFrame) -> dict[str, int]:
        """Resolve every ``lines:`` alias to exactly one ``metric_id`` (section + name). Returns alias → id."""
        out: dict[str, int] = {}
        for alias, (section, name) in self.lines.items():
            hit = metric[(metric["section"] == section) & (metric["name"] == name)]
            if len(hit) != 1:
                raise RegistryError(f"line {alias!r} = {section}/{name} resolves to {len(hit)} metric ids (want 1)")
            out[alias] = int(hit["metric_id"].iloc[0])
        return out


def _parse_date(name: str, v: Any, source: str) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError as exc:
        raise RegistryError(f"{source}: {name!r} trainable_from {v!r} is not an ISO date") from exc


def _parse_feature(name: str, meta: Mapping[str, Any], lines: Mapping[str, Any], source: str) -> Feature:
    missing = [k for k in REQUIRED if k not in meta]
    if missing:
        raise RegistryError(f"{source}: feature {name!r} lacks {missing}")
    if meta["kind"] not in KINDS:
        raise RegistryError(f"{source}: {name!r} kind {meta['kind']!r} not in {KINDS}")
    if meta["prior"] not in (1, -1, None):
        raise RegistryError(f"{source}: {name!r} prior must be 1, -1 or null, got {meta['prior']!r}")
    if meta["set"] not in SETS:
        raise RegistryError(f"{source}: {name!r} set {meta['set']!r} not in {SETS}")
    if not meta["family"]:
        raise RegistryError(f"{source}: {name!r} has an empty family")
    layouts = meta.get("layouts", list(LAYOUTS))
    bad = [x for x in layouts if x not in LAYOUTS]
    if bad or not layouts:
        raise RegistryError(f"{source}: {name!r} layouts {layouts!r} invalid (allowed {LAYOUTS})")
    feature_lines = meta.get("lines") or []
    unknown = [a for a in feature_lines if a not in lines]
    if unknown:
        raise RegistryError(f"{source}: {name!r} refers to undeclared lines {unknown}")
    return Feature(
        name=name, family=str(meta["family"]), prior=meta["prior"], kind=meta["kind"], category=str(meta["category"]),
        layouts=tuple(layouts), set=meta["set"], growth=bool(meta.get("growth", False)),
        need_fy=int(meta.get("need_fy", 1)), lines=tuple(feature_lines),
        complete=bool(meta.get("complete", meta["set"] == "core")), source=source,
        trainable_from=_parse_date(name, meta.get("trainable_from"), source),
    )  # fmt: skip


def load_registry(config_dir: Path | None = None) -> Registry:
    """Merge every ``features_*.yaml`` (flat directory). Duplicate feature names or line aliases are errors."""
    if config_dir is None:
        from ideation.config import REPO_ROOT

        config_dir = REPO_ROOT / "config"
    feats: dict[str, Feature] = {}
    lines: dict[str, tuple[str, str]] = {}
    cleaning: dict[str, Any] = {}
    files = sorted(Path(config_dir).glob("features_*.yaml"))
    if not files:
        raise RegistryError(f"no features_*.yaml under {config_dir}")
    for p in files:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        raw_lines = doc.get("lines") or {}
        for alias, spec in raw_lines.items():
            section, _, name = str(spec).partition("/")
            if not name:
                raise RegistryError(f"{p.name}: line {alias!r} must be '<section>/<name>'")
            if alias in lines:
                raise RegistryError(f"{p.name}: duplicate line alias {alias!r}")
            lines[alias] = (section, name)
        for k, v in (doc.get("cleaning") or {}).items():
            if k in cleaning and cleaning[k] != v:
                raise RegistryError(f"{p.name}: cleaning key {k!r} already set to {cleaning[k]!r} by another file")
            cleaning[k] = v
        for name, meta in (doc.get("features") or {}).items():
            if name in feats:
                raise RegistryError(f"{p.name}: feature {name!r} already defined in {feats[name].source}")
            feats[name] = _parse_feature(name, meta or {}, lines, p.name)
    return Registry(feats, lines, cleaning, tuple(p.name for p in files))
