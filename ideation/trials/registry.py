"""Pre-registered trials (05 §3.1, 02 §5.3): load the trial YAMLs, hash each resolved config, insert them into
``ideation.experiment_trial`` with status ``registered`` before any fit, and refuse to train an unregistered hash.

The hashed config of a trial = ``{family, horizon, num, id} ⊕ defaults ⊕ the entry``, plus ``params`` = the zoo's
fixed hyperparameters for that learner and horizon (so a hyperparameter change is a new hash, i.e. a new trial).
The trial count N of a family (for the DSR) counts every row, whatever its status, and is never reset.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ideation.config import REPO_ROOT, canonical_json
from ideation.db.writer import GuardedConnection
from ideation.models.zoo import learner_params

FAMILY_FILES = {"1Y-june": "trials_1y.yaml", "tactical-3M": "trials_tactical.yaml"}
ZOO_HORIZON = {"1Y": "1Y", "3M": "tactical", "1M": "tactical"}


class UnregisteredTrialError(RuntimeError):
    """``train`` was asked to fit a config whose hash is not registered for the family."""


@dataclass(frozen=True)
class TrialSpec:
    family: str
    num: int
    trial_id: str
    horizon: str
    anchor_family: str
    config: dict[str, Any]

    @property
    def config_hash(self) -> str:
        return config_hash(self.config)


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def resolve(doc: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Merged, JSON-normalised config of one entry (dates → ISO strings, tuples → lists)."""
    cfg: dict[str, Any] = {"family": doc["family"], "horizon": doc["horizon"], **doc.get("defaults", {}), **entry}
    learner = entry.get("learner")
    if learner is not None:
        cfg["params"] = {**learner_params(learner, ZOO_HORIZON[doc["horizon"]]), **entry.get("params", {})}
    if "consensus" in entry:
        cfg["members"] = list(doc["consensus_members"])
    return json.loads(canonical_json(cfg))


def load_trials(path: Path) -> list[TrialSpec]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs = []
    for entry in doc["trials"]:
        cfg = resolve(doc, entry)
        specs.append(TrialSpec(doc["family"], int(entry["num"]), str(entry["id"]), doc["horizon"],
                               str(cfg["anchor_family"]), cfg))  # fmt: skip
    validate(specs, int(doc["n_declared"]))
    return specs


def load_family(family: str, config_dir: Path | None = None) -> list[TrialSpec]:
    if family not in FAMILY_FILES:
        raise KeyError(f"unknown family {family!r}; known: {sorted(FAMILY_FILES)}")
    specs = load_trials((config_dir or REPO_ROOT / "config") / FAMILY_FILES[family])
    if any(s.family != family for s in specs):
        raise ValueError(f"{FAMILY_FILES[family]} does not declare family {family!r}")
    return specs


def validate(specs: Sequence[TrialSpec], n_declared: int) -> None:
    nums = [s.num for s in specs]
    ids = [s.trial_id for s in specs]
    hashes = [s.config_hash for s in specs]
    if nums != list(range(1, n_declared + 1)):
        raise ValueError(f"trial numbers must be 1..{n_declared} in order, got {nums}")
    for name, values in (("id", ids), ("config hash", hashes)):
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate trial {name} in the family")
    for s in specs:
        kinds = [k for k in ("learner", "model", "consensus") if k in s.config]
        if len(kinds) != 1:
            raise ValueError(f"trial {s.trial_id} needs exactly one of learner/model/consensus, got {kinds}")
    members = {m for s in specs for m in s.config.get("members", [])}
    if not members <= set(ids):
        raise ValueError(f"consensus members not in the family: {sorted(members - set(ids))}")


@dataclass(frozen=True)
class RegisterResult:
    inserted: tuple[tuple[int, str], ...]
    """(trial_id, id) of newly registered rows."""
    existing: tuple[str, ...]
    n_family: int


_INSERT = (
    "insert into ideation.experiment_trial (family, config_hash, config_json, horizon, anchor_family, status)"
    " values (%s, %s, %s::jsonb, %s, %s, 'registered') on conflict (family, config_hash) do nothing returning trial_id"
)


def register(conn: GuardedConnection, specs: Sequence[TrialSpec]) -> RegisterResult:
    """Insert every spec (idempotent on (family, config_hash)) in one transaction."""
    inserted, existing = [], []
    with conn.transaction():
        for s in specs:
            row = conn.execute(
                _INSERT, (s.family, s.config_hash, canonical_json(s.config), s.horizon, s.anchor_family)
            ).fetchone()
            if row:
                inserted.append((int(row[0]), s.trial_id))
            else:
                existing.append(s.trial_id)
    families = {s.family for s in specs}
    n = sum(family_n(conn, f) for f in families)
    return RegisterResult(tuple(inserted), tuple(existing), n)


def family_n(conn: GuardedConnection, family: str) -> int:
    """Trials ever registered in the family (all statuses): the DSR's N."""
    row = conn.execute("select count(*) from ideation.experiment_trial where family = %s", (family,)).fetchone()
    return int(row[0]) if row else 0


def assert_registered(conn: GuardedConnection, family: str, hash_: str) -> int:
    """``trial_id`` of a registered config; raises :class:`UnregisteredTrialError` otherwise (``train`` calls this
    before any fit)."""
    row = conn.execute(
        "select trial_id from ideation.experiment_trial where family = %s and config_hash = %s", (family, hash_)
    ).fetchone()
    if row is None:
        raise UnregisteredTrialError(
            f"config {hash_[:12]} is not registered in family {family!r}: run `ideation trials register` first"
        )
    return int(row[0])


def registered(conn: GuardedConnection, family: str) -> list[tuple[int, str, str, str]]:
    """(trial_id, id, status, config_hash) of the family, by trial_id."""
    rows = conn.execute(
        "select trial_id, config_json->>'id', status, config_hash from ideation.experiment_trial"
        " where family = %s order by trial_id",
        (family,),
    ).fetchall()
    return [(int(a), str(b), str(c), str(d)) for a, b, c, d in rows]
