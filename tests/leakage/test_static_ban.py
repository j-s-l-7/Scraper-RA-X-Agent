"""02 T-17 (static half) + 03 §C10 import contract + 02 §7 clock ban. AST scans of the model-building packages.

* No string constant in ideation/{features,labels,universe,models,cv,eval,backtest} may name a banned source.
* features/models/universe must not import ideation.lake, ideation.labels or psycopg (models' training entry
  ``ideation/models/train.py`` may import labels). Feature builders take a PitView, never a connection.
* cv/eval/models/rank/trials must not import ideation.lake or psycopg (E1, 05 §10.3); features/models/universe/rank/cv
  must not import the post-anchor ``ideation.outcomes`` (05 §5).
* features/labels/universe must not read the clock (date.today / datetime.now / utcnow / time.time).

Each rule has a self-test on a planted module, so the scan cannot pass vacuously while the dirs are near-empty.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ideation.config import REPO_ROOT
from ideation.lake.bans import find_banned

PKG = REPO_ROOT / "ideation"
BAN_SCANNED = ("features", "labels", "universe", "models", "cv", "eval", "backtest", "rank", "regime", "tactical",
               "outcomes")  # fmt: skip
VENDOR_MODULES = ("ideation/features/events_vendor.py", "ideation/outcomes/analyst.py")
BAN_ALLOWLIST: frozenset[str] = frozenset(VENDOR_MODULES)  # 05 O5: exactly one feature module + one outcome module
IMPORT_RESTRICTED = ("features", "models", "universe", "regime")
FORBIDDEN_IMPORTS = ("ideation.lake", "ideation.labels", "psycopg", "psycopg2")
IMPORT_EXCEPTIONS = {("ideation/models/train.py", "ideation.labels")}
LAKE_FREE = ("cv", "eval", "models", "rank", "trials")
LAKE_IMPORTS = ("ideation.lake", "psycopg", "psycopg2")
OUTCOME_FREE = ("features", "models", "universe", "rank", "cv", "regime")
OUTCOME_IMPORTS = ("ideation.outcomes",)
OUTCOME_PKG_FORBIDDEN = ("ideation.models", "ideation.rank", "ideation.cv", "ideation.lake", "psycopg", "psycopg2")
OUTCOME_WRITE_OK = {"ideation.outcome"}
CLOCK_SCANNED = ("features", "labels", "universe", "regime", "outcomes")
CLOCK_ATTRS = {("date", "today"), ("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"), ("time", "time"),
               ("Timestamp", "now"), ("Timestamp", "today")}  # fmt: skip


def py_files(subdirs: tuple[str, ...], root: Path = PKG) -> list[Path]:
    return sorted(p for d in subdirs if (root / d).is_dir() for p in (root / d).rglob("*.py"))


def banned_strings(path: Path) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            hits.extend(find_banned(node.value))
            if node.value.strip().upper() == "TTM":
                hits.append("TTM")
    return hits


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent).with_suffix("")
    parts = list(rel.parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(path: Path, root: Path = PKG) -> list[str]:
    """Absolute module names imported by ``path`` (relative imports resolved against its package)."""
    here = _module_name(path, root).split(".")
    package = here if path.name == "__init__.py" else here[:-1]
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            mod = ".".join([*base, node.module] if node.module else base)
            out.append(mod)
            out.extend(f"{mod}.{a.name}" for a in node.names)
    return out


def forbidden_imports(path: Path, root: Path = PKG, forbidden: tuple[str, ...] = FORBIDDEN_IMPORTS) -> list[str]:
    rel = path.relative_to(root.parent).as_posix()
    hits = []
    for mod in imported_modules(path, root):
        for banned in forbidden:
            if (mod == banned or mod.startswith(banned + ".")) and (rel, banned) not in IMPORT_EXCEPTIONS:
                hits.append(mod)
    return hits


def clock_reads(path: Path) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute):
            owner = node.value.attr if isinstance(node.value, ast.Attribute) else getattr(node.value, "id", None)
            if (owner, node.attr) in CLOCK_ATTRS:
                hits.append(f"{owner}.{node.attr}")
    return hits


# ── the real scans ───────────────────────────────────────────────────────────────────────────────────────────
def test_scanned_packages_exist() -> None:
    for d in ("features", "labels", "universe", "models", "cv", "eval", "rank", "trials", "regime", "outcomes"):
        assert (PKG / d / "__init__.py").is_file(), d


def test_static_ban() -> None:
    hits = {
        p.relative_to(REPO_ROOT).as_posix(): banned_strings(p)
        for p in py_files(BAN_SCANNED)
        if p.relative_to(REPO_ROOT).as_posix() not in BAN_ALLOWLIST
    }
    assert {k: v for k, v in hits.items() if v} == {}


def test_ban_allowlist_is_the_two_vendor_modules() -> None:
    """05 O5 / §5.1: exactly the feature module and the outcome module are allow-listed; each really names the vendor
    columns (not vacuous); no other scanned module may (``test_static_ban``); both still obey the import and clock
    rules and the outcome module obeys the outcomes import contract."""
    assert set(VENDOR_MODULES) == BAN_ALLOWLIST
    for rel in VENDOR_MODULES:
        mod = REPO_ROOT / rel
        assert {"eps_estimate", "eps_reported"} & set(banned_strings(mod)), rel
        assert forbidden_imports(mod) == [] and clock_reads(mod) == [], rel
        assert mod in py_files(CLOCK_SCANNED)
    assert REPO_ROOT / VENDOR_MODULES[0] in py_files(IMPORT_RESTRICTED)
    assert REPO_ROOT / VENDOR_MODULES[1] in py_files(("outcomes",))
    non_vendor = [p for p in py_files(("outcomes",)) if p.relative_to(REPO_ROOT).as_posix() not in BAN_ALLOWLIST]
    assert {p.name for p in non_vendor} >= {"fundamental.py", "cli.py"}  # scanned for real, not exempt


def test_import_contract() -> None:
    hits = {p.relative_to(REPO_ROOT).as_posix(): forbidden_imports(p) for p in py_files(IMPORT_RESTRICTED)}
    assert {k: v for k, v in hits.items() if v} == {}


def test_lake_free_packages() -> None:
    """E1 packages run on frames only: no lake reader, no database driver."""
    hits = {
        p.relative_to(REPO_ROOT).as_posix(): forbidden_imports(p, forbidden=LAKE_IMPORTS) for p in py_files(LAKE_FREE)
    }
    assert {k: v for k, v in hits.items() if v} == {}


def test_outcomes_are_isolated() -> None:
    """05 §5: outcomes read the future by design; nothing that builds features, fits or ranks may import them."""
    hits = {
        p.relative_to(REPO_ROOT).as_posix(): forbidden_imports(p, forbidden=OUTCOME_IMPORTS)
        for p in py_files(OUTCOME_FREE)
    }
    assert {k: v for k, v in hits.items() if v} == {}


def test_outcomes_import_contract_and_write_target() -> None:
    """05 §5: outcomes import no model/rank/cv/lake/driver module and write only to ``ideation.outcome``."""

    files = py_files(("outcomes",))
    assert files
    bad = {p.relative_to(REPO_ROOT).as_posix(): forbidden_imports(p, forbidden=OUTCOME_PKG_FORBIDDEN) for p in files}
    assert {k: v for k, v in bad.items() if v} == {}
    for p in files:
        assert write_targets_in(p) <= OUTCOME_WRITE_OK, p


def write_targets_in(path: Path) -> set[str]:
    import re

    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.update(re.findall(r"(?:insert\s+into|delete\s+from|update)\s+([a-z_]+\.[a-z_]+)", node.value, re.I))
            out.update(re.findall(r"copy_rows\(\s*\"([a-z_.]+)\"", node.value))
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "copy_rows":
            a0 = node.args[0]
            if isinstance(a0, ast.Constant):
                out.add(str(a0.value))
    return out


def test_no_clock_reads() -> None:
    hits = {p.relative_to(REPO_ROOT).as_posix(): clock_reads(p) for p in py_files(CLOCK_SCANNED)}
    assert {k: v for k, v in hits.items() if v} == {}


# ── self-tests on planted modules ────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def planted(tmp_path: Path) -> Path:
    root = tmp_path / "ideation"
    (root / "features").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "features" / "__init__.py").write_text("")
    return root


def test_scanner_catches_planted_banned_strings(planted: Path) -> None:
    mod = planted / "features" / "bad.py"
    mod.write_text(
        'Q = "select * from equity.market_snapshot"\n'
        'P = "TTM"\n'
        'def f(df):\n    return df[df["is_active"]]\n'
        'U = "select * from equity.universe"\n'
    )
    assert set(banned_strings(mod)) == {"market_snapshot", "TTM", "is_active", "equity.universe"}
    assert py_files(("features",), planted) == sorted(planted.joinpath("features").glob("*.py"))


def test_planted_vendor_literal_outside_the_allowlist_fails(planted: Path) -> None:
    mod = planted / "features" / "sneaky.py"
    mod.write_text('COLS = ["eps_estimate", "surprise_pct"]\n')
    assert set(banned_strings(mod)) == {"eps_estimate", "surprise_pct"}
    assert "ideation/features/sneaky.py" not in BAN_ALLOWLIST
    (planted.parent / "ideation" / "outcomes").mkdir()
    out = planted / "outcomes" / "fundamental.py"
    out.write_text('COLS = ["eps_estimate"]\n')
    assert set(banned_strings(out)) == {"eps_estimate"}
    assert "ideation/outcomes/fundamental.py" not in BAN_ALLOWLIST


def test_planted_outcome_writes_and_imports_are_caught(planted: Path) -> None:
    (planted / "outcomes").mkdir()
    mod = planted / "outcomes" / "bad.py"
    mod.write_text(
        'import psycopg\nfrom ideation.models import zoo\nq = "delete from ideation.label where x"\n'
        'def f(conn):\n    conn.copy_rows("ideation.oos_prediction", [])\n'
    )
    assert set(forbidden_imports(mod, planted, OUTCOME_PKG_FORBIDDEN)) >= {"psycopg", "ideation.models"}
    assert write_targets_in(mod) == {"ideation.label", "ideation.oos_prediction"}


def test_scanner_catches_planted_imports(planted: Path) -> None:
    mod = planted / "features" / "bad.py"
    mod.write_text("import psycopg\nfrom ideation.lake import adapter\nfrom ..labels import build\nimport numpy\n")
    assert set(forbidden_imports(mod, planted)) >= {
        "psycopg",
        "ideation.lake",
        "ideation.lake.adapter",
        "ideation.labels",
    }
    assert "numpy" not in forbidden_imports(mod, planted)
    (planted / "rank").mkdir()
    rk = planted / "rank" / "bad.py"
    rk.write_text("from psycopg import sql\nfrom ideation.outcomes import analyst\nfrom ideation.labels import x\n")
    assert set(forbidden_imports(rk, planted, LAKE_IMPORTS)) == {"psycopg", "psycopg.sql"}
    assert set(forbidden_imports(rk, planted, OUTCOME_IMPORTS)) == {"ideation.outcomes", "ideation.outcomes.analyst"}


def test_scanner_catches_planted_clock_reads(planted: Path) -> None:
    mod = planted / "features" / "bad.py"
    mod.write_text("import datetime as dt\nfrom datetime import date\na = date.today()\nb = dt.datetime.now()\n")
    assert set(clock_reads(mod)) == {"date.today", "datetime.now"}
