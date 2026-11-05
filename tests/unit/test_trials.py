"""05 §3.1/§3.5/§3.7/§11: the trial lists load, validate and hash deterministically; the CLI dry run needs no lake."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ideation.cli import app
from ideation.config import REPO_ROOT
from ideation.models import zoo
from ideation.trials.registry import config_hash, load_family, load_trials

CONFIG = REPO_ROOT / "config"


def test_families_load_with_declared_n() -> None:
    one_y = load_family("1Y-june")
    tactical = load_family("tactical-3M")
    assert len(one_y) == 50
    assert len(tactical) == 25
    assert [s.num for s in one_y] == list(range(1, 51))
    assert len({s.config_hash for s in one_y}) == 50
    ids = {s.trial_id: s for s in one_y}
    assert {"P1_COMP_EW", "RF_GLOBAL", "RF_GLOBAL_EXT", "CONS_MAJ", "ENS_RANKAVG"} <= set(ids)
    assert ids["RF_GLOBAL_9010SEC"].num == 49
    assert ids["RF_GLOBAL_EXT_9010SEC"].num == 50
    assert ids["RF_GLOBAL_9010SEC"].config["ninety_ten_sec"] is True
    t_ids = {s.trial_id: s for s in tactical}
    assert t_ids["RF_T0_9010SEC"].num == 25
    assert all(s.anchor_family == "monthly" for s in tactical)
    assert ids["RF_GLOBAL_MARCH"].anchor_family == "march"


def test_spec_grid_12_to_32() -> None:
    one_y = {s.num: s for s in load_family("1Y-june")}
    grid = [(one_y[n].config["learner"], one_y[n].config["spec"]) for n in range(12, 33)]
    learners = ["rf", "pridge", "ridge", "lgbm", "xgb", "mlp", "svr"]
    assert grid == [(lr, sp) for lr in learners for sp in ("global", "industry", "hybrid")]


def test_fixed_hyperparameters_are_hashed() -> None:
    one_y = {s.trial_id: s for s in load_family("1Y-june")}
    tactical = {s.trial_id: s for s in load_family("tactical-3M")}
    assert one_y["RF_GLOBAL"].config["params"]["n_estimators"] == 500
    assert tactical["RF_T0"].config["params"]["min_samples_leaf"] == 100
    assert tactical["MLP_T0"].config["params"]["max_iter"] == 30
    assert one_y["MLP_GLOBAL"].config["params"]["early_stopping"] is False
    assert one_y["CONS_MAJ"].config["members"][-1] == "P1_COMP_DOSSIER"
    assert len(one_y["CONS_MAJ"].config["members"]) == 10


def test_hash_is_deterministic_and_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    a = {s.trial_id: s.config_hash for s in load_family("1Y-june")}
    assert a == {s.trial_id: s.config_hash for s in load_family("1Y-june")}
    assert config_hash({"b": 1, "a": [1, 2]}) == config_hash({"a": [1, 2], "b": 1})
    changed = copy.deepcopy(zoo.FIXED_PARAMS)
    changed["rf"]["1Y"]["min_samples_leaf"] = 26
    monkeypatch.setattr(zoo, "FIXED_PARAMS", changed)
    b = {s.trial_id: s.config_hash for s in load_family("1Y-june")}
    rf_trials = {k for k, s in a.items() if k.startswith("RF_")}
    assert {k for k in a if a[k] != b[k]} == rf_trials  # a hyperparameter change is a new trial


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "trials_x.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_validation_errors(tmp_path: Path) -> None:
    doc = yaml.safe_load((CONFIG / "trials_tactical.yaml").read_text())
    bad = copy.deepcopy(doc)
    bad["n_declared"] = 24
    with pytest.raises(ValueError, match="numbers"):
        load_trials(_write(tmp_path, bad))
    dup = copy.deepcopy(doc)
    dup["trials"][1]["id"] = dup["trials"][0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        load_trials(_write(tmp_path, dup))
    two = copy.deepcopy(doc)
    two["trials"][1]["model"] = "p1_comp_ew"
    with pytest.raises(ValueError, match="exactly one"):
        load_trials(_write(tmp_path, two))
    with pytest.raises(KeyError, match="unknown family"):
        load_family("1Y-dec")


def test_cli_dry_run_needs_no_lake() -> None:
    res = CliRunner().invoke(app, ["trials", "register", "--family", "tactical-3M", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "25 trials (dry run" in res.output
    assert "RF_T0_9010SEC" in res.output
