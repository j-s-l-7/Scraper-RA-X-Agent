"""P6 static contracts (05 §7.4, 02 P5 row): the live-only ideas package (LLM summary, overlay) is never imported by a
backtest-path package; the live `predict` path uses the PitStore (no lake reader, no wall clock) and reads labels only
for the challenger fit through the sealed-aware loader."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ideation.config import REPO_ROOT
from tests.leakage.test_static_ban import PKG, forbidden_imports, py_files

BACKTEST_PATH = ("features", "models", "cv", "eval", "rank", "backtest", "tactical", "bakeoff", "regime", "universe",
                 "labels", "trials", "outcomes", "pit", "text", "report")  # fmt: skip
LIVE_ONLY = ("ideation.ideas", "ideation.predict")


def test_backtest_packages_never_import_the_live_ideas_or_predict_packages() -> None:
    hits = {
        p.relative_to(REPO_ROOT).as_posix(): forbidden_imports(p, forbidden=LIVE_ONLY) for p in py_files(BACKTEST_PATH)
    }
    assert {k: v for k, v in hits.items() if v} == {}
    assert py_files(("features", "models", "cv", "eval", "rank"))  # the scan is not vacuous


def test_llm_summary_is_imported_only_by_the_ideas_cli() -> None:
    importers = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in sorted(PKG.rglob("*.py"))
        if forbidden_imports(p, forbidden=("ideation.ideas.llm_summary",)) and p.name != "llm_summary.py"
    ]
    assert importers == ["ideation/ideas/cli.py"]


def test_predict_uses_the_pitstore_not_the_lake_and_never_reads_the_clock() -> None:
    from tests.leakage.test_static_ban import LAKE_IMPORTS, clock_reads

    files = py_files(("predict",))
    assert files
    bad = {p.name: forbidden_imports(p, forbidden=(*LAKE_IMPORTS, "ideation.outcomes")) for p in files}
    assert {k: v for k, v in bad.items() if v} == {}
    assert {p.name: clock_reads(p) for p in files if clock_reads(p)} == {}


def test_only_the_challenger_reads_labels_and_always_unsealed() -> None:
    users = {p.name for p in py_files(("predict",)) if forbidden_imports(p, forbidden=("ideation.labels",))}
    assert users == {"challenger.py", "cal.py"}
    src = (PKG / "predict" / "challenger.py").read_text()
    assert "unsealed_only(" in src and "load_labels(" in src
    cal_src = (PKG / "predict" / "cal.py").read_text()
    assert "load_labels" not in cal_src  # calibration reads stored OOS rows, never labels


def test_live_scoring_reuses_the_backtest_entry_points() -> None:
    """02 P5: the live path is the same code as the backtest path (parity itself is the lake test)."""
    src = (PKG / "predict" / "score.py").read_text()
    for needle in (
        "bakeoff.data import",
        "build_matrix",
        "bakeoff.models import build_model",
        "tactical.data import",
        "build_design",
        "tactical.engine import build_model",
    ):
        assert needle in src, needle
    live = (PKG / "predict" / "live.py").read_text()
    for needle in ("build_fundamental", "build_universe", "build_view"):
        assert needle in live


def test_planted_llm_import_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "ideation"
    (root / "rank").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    mod = root / "rank" / "bad.py"
    mod.write_text("from ideation.ideas import llm_summary\nimport ideation.ideas.overlay\n")
    hits = forbidden_imports(mod, root, LIVE_ONLY)
    assert "ideation.ideas.llm_summary" in hits and "ideation.ideas.overlay" in hits


def test_ideas_package_has_no_backtest_dependencies_on_sealed_labels() -> None:
    for p in py_files(("ideas",)):
        tree = ast.parse(p.read_text())
        calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "load_labels" not in calls, p


@pytest.mark.parametrize("name", ["llm_summary.py", "overlay.py", "select.py", "render.py", "persist.py", "cli.py"])
def test_ideas_modules_exist(name: str) -> None:
    assert (PKG / "ideas" / name).is_file()
