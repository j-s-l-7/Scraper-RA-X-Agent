"""PT1 read-only static test (05 §10.3): ``ideation/text`` cannot write outside its outputs, import a database
driver, call the network or an LLM, read the clock, or reach beyond ``data/documents``.

* ``reader.py`` is the only module that opens the Scraper's files, and only in text read mode.
* Parquet/dir creation is allowed in ``extract.py`` and ``annual.py`` (PT3) only, and only on the ``out_dir`` /
  ``out_path`` / ``cache_path``
  it derives from ``IDEATION_DATA_DIR/text``.
* ``ideation.lake`` (the adapter) is imported by ``cli.py`` only; ``psycopg`` by nobody.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ideation.config import REPO_ROOT
from tests.leakage.test_static_ban import clock_reads, imported_modules

TEXT = REPO_ROOT / "ideation" / "text"
NETWORK_AND_LLM = ("psycopg", "psycopg2", "requests", "httpx", "urllib", "socket", "openai", "anthropic", "subprocess")
WRITE_ATTRS = {
    "write_text", "write_bytes", "unlink", "rmdir", "rename", "touch", "symlink_to", "chmod", "to_csv", "to_pickle",
    "to_json", "to_feather", "to_sql", "to_excel", "write_table", "writelines", "removedirs", "rmtree", "copyfile",
    "system", "popen",
}  # fmt: skip
FS_MODULES = {"os", "shutil"}  # any call on these is a filesystem side effect
OUTPUT_ONLY = {"to_parquet": {"extract.py", "annual.py"}, "mkdir": {"extract.py", "annual.py"}}
BAD_MODES = set("wax+")


def py_files(root: Path = TEXT) -> list[Path]:
    return sorted(root.glob("*.py"))


def write_violations(path: Path, *, allowed_outputs: dict[str, set[str]] = OUTPUT_ONLY) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name == "open":
            mode = None
            if len(node.args) > 1:
                arg = node.args[1]
                mode = arg.value if isinstance(arg, ast.Constant) else "?"
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = kw.value.value if isinstance(kw.value, ast.Constant) else "?"
            if mode is None or mode == "?" or BAD_MODES & set(str(mode)):
                hits.append(f"open(mode={mode!r}) at line {node.lineno}")
        elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in FS_MODULES:
            hits.append(f"{fn.value.id}.{name} at line {node.lineno}")
        elif name in allowed_outputs:
            if path.name not in allowed_outputs[name]:
                hits.append(f"{name} at line {node.lineno}")
        elif name in WRITE_ATTRS:
            hits.append(f"{name} at line {node.lineno}")
    return hits


def forbidden_module_hits(path: Path, root: Path = REPO_ROOT / "ideation") -> list[str]:
    hits = []
    for mod in imported_modules(path, root):
        if any(mod == b or mod.startswith(b + ".") for b in NETWORK_AND_LLM):
            hits.append(mod)
        if (mod == "ideation.lake" or mod.startswith("ideation.lake.")) and path.name != "cli.py":
            hits.append(mod)
        if mod == "ideation.db" or mod.startswith("ideation.db."):
            hits.append(mod)
    return hits


def string_hits(path: Path) -> list[str]:
    bad = ("Scraper", ".env", "secrets", "library.sqlite", "xagent", "X-Agent")
    out = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.extend(f"{b!r} in string at line {node.lineno}" for b in bad if b in node.value)
    return out


def test_text_package_present() -> None:
    assert {p.name for p in py_files()} >= {"reader.py", "clean.py", "lexicon.py", "extract.py", "cli.py"}


def test_text_cannot_write_outside_outputs() -> None:
    assert {p.name: write_violations(p) for p in py_files() if write_violations(p)} == {}


def test_reader_opens_only_gzip_text_read() -> None:
    src = (TEXT / "reader.py").read_text(encoding="utf-8")
    assert 'gzip.open(resolve_text_path(root, text_path), "rt"' in src
    assert 'DOCS_SUBDIR = ("data", "documents")' in src
    assert [p.name for p in py_files() if "gzip" in imported_modules(p, REPO_ROOT / "ideation")] == ["reader.py"]


def test_text_imports_no_driver_network_llm_or_lake_writer() -> None:
    assert {p.name: forbidden_module_hits(p) for p in py_files() if forbidden_module_hits(p)} == {}


def test_text_reads_no_clock_and_no_foreign_paths() -> None:
    assert {p.name: clock_reads(p) for p in py_files() if clock_reads(p)} == {}
    assert {p.name: string_hits(p) for p in py_files() if string_hits(p)} == {}


# ── self-tests: the scanners are not vacuous ────────────────────────────────────────────────────────────────
@pytest.fixture
def planted(tmp_path: Path) -> Path:
    d = tmp_path / "ideation" / "text"
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize(
    "code",
    [
        "open('x', 'w')",
        "open('x', mode='ab')",
        "open('x')",  # default mode is fine for text but an unexplicit mode is refused (must say 'r'/'rt')
        "gzip.open(p, 'wt')",
        "p.write_text('x')",
        "df.to_parquet('y')",
        "os.remove('x')",
        "shutil.rmtree('x')",
    ],
)
def test_write_scanner_catches(planted: Path, code: str) -> None:
    f = planted / "bad.py"
    f.write_text(f"def f(p, df):\n    {code}\n")
    assert write_violations(f), code


def test_write_scanner_allows_reads_and_extract_outputs(planted: Path) -> None:
    ok = planted / "ok.py"
    ok.write_text("def f(p):\n    return gzip.open(p, 'rt'), open(p, 'rb')\n")
    assert write_violations(ok) == []
    ex = planted / "extract.py"
    ex.write_text("def f(df, out):\n    out.mkdir()\n    df.to_parquet(out)\n")
    assert write_violations(ex) == []


def test_import_and_string_scanners_catch(planted: Path) -> None:
    f = planted / "reader.py"
    f.write_text(
        "import psycopg\nfrom ideation.lake import adapter\nimport openai\nX = '~/Scraper - Screener.in/.env'\n"
    )
    assert len(forbidden_module_hits(f, planted.parent)) >= 3
    assert len(string_hits(f)) == 2
    (planted / "x.py").write_text("import datetime\nx = datetime.datetime.now()\n")
    assert clock_reads(planted / "x.py")
