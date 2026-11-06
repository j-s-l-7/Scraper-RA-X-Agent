"""scripts/secret_scan.py: flags planted secrets and data files; the repo itself is clean (--all).

Planted keys are assembled at runtime so this file never contains a literal key prefix.
"""

from __future__ import annotations

import importlib.util
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from ideation.config import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "secret_scan.py"
ANT = "sk" + "-ant-"

spec = importlib.util.spec_from_file_location("secret_scan", SCRIPT)
assert spec
assert spec.loader
ss = importlib.util.module_from_spec(spec)
sys.modules["secret_scan"] = ss
spec.loader.exec_module(ss)


def run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def test_flags_planted_anthropic_key_in_temp_file(tmp_path: Path) -> None:
    f = tmp_path / "planted.py"
    f.write_text(f'KEY = "{ANT}xxx"\n')
    res = run(str(f))
    assert res.returncode == 1
    assert "anthropic-key" in res.stderr
    assert f"{ANT}xxx" not in res.stderr  # never echoes the secret


def test_repo_is_clean_all() -> None:
    res = run("--all")
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize(
    ("content", "rule"),
    [
        (f"k = '{ANT}api03-" + "A" * 60 + "'", "anthropic-key"),
        ("-----" + "BEGIN RSA PRIVATE KEY-----", "private-key"),
        ("-----" + "BEGIN PRIVATE KEY-----", "private-key"),
        ("dsn = 'postgresql://brainx:" + "s3cret" + "@localhost:5433/equity_lake'", "password-in-url"),
        ("t = 'ghp_" + "a1B2" * 9 + "'", "github-token"),
        ("id = 'AKIA" + "ABCDEFGHIJKLMNOP" + "'", "aws-access-key-id"),
        ("x = '" + secrets.token_urlsafe(40) + "'", "high-entropy-string"),
    ],
)
def test_rules(content: str, rule: str) -> None:
    assert rule in {f.rule for f in ss.scan_text("src/x.py", content)}


@pytest.mark.parametrize(
    "content",
    [
        "dsn = 'postgresql://ideation_rw@localhost:5433/equity_lake'",  # no password
        "sha = '" + "0123456789abcdef" * 4 + "'",  # hex sha256: 4 bits/char
        "path = '/Users/brainx/Investment Ideation Model - Personal Use/scratchpad/src/Scraper/completeness.py'",
        f"line with marker {ANT}{'B' * 50}  # secret-scan: " + "allow",
    ],
)
def test_benign(content: str) -> None:
    assert ss.scan_text("src/x.py", content) == []


def test_docs_may_describe_patterns_but_not_contain_keys() -> None:
    describe = f"Fails on `{ANT}`, private keys and passwords in DSNs."
    assert ss.scan_text("docs/plan/01_ARCHITECTURE_PLAN.md", describe) == []
    real = f"key: {ANT}api03-{'Z' * 60}"
    assert ss.scan_text("docs/plan/01_ARCHITECTURE_PLAN.md", real)
    assert ss.scan_text("src/x.py", describe)  # outside docs even a bare prefix is flagged


def test_high_entropy_exempt_in_uv_lock() -> None:
    token = secrets.token_urlsafe(40)
    assert ss.scan_text("uv.lock", f"hash = '{token}'") == []
    assert ss.scan_text("uv.lock", f"k = '{ANT}api03-{'Z' * 60}'")  # token rules still apply


@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", "x/prices.parquet", "a.csv", "db/lake.sqlite", "lake.sqlite-wal", "m.pkl", "data/x.txt",
     "reports/2026/r.md", "nested/logs/x.jsonl", "id_rsa", "cert.pem"],
)  # fmt: skip
def test_forbidden_paths(path: str) -> None:
    assert ss.check_path(path, 10)


@pytest.mark.parametrize("path", [".env.example", "ideation/reports/render.py", "ideation/config.py", "uv.lock"])
def test_allowed_paths(path: str) -> None:
    assert ss.check_path(path, 10) == []


def test_size_limit() -> None:
    assert ss.check_path("big.txt", 1_000_001)
    assert ss.check_path("ok.txt", 1_000_000) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "ok.py").write_text("x = 1\n")
    _git(tmp_path, "add", "ok.py")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_staged_reads_the_index_not_the_work_tree(temp_repo: Path) -> None:
    f = temp_repo / "leak.py"
    f.write_text(f'K = "{ANT}staged"\n')
    _git(temp_repo, "add", "leak.py")
    f.write_text("K = None\n")  # work tree is clean, the index is not
    res = run("--staged", cwd=temp_repo)
    assert res.returncode == 1
    assert "anthropic-key" in res.stderr


def test_range_catches_a_secret_removed_in_a_later_commit(temp_repo: Path) -> None:
    base = subprocess.run(
        ["git", "-C", str(temp_repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    f = temp_repo / "leak.py"
    f.write_text(f'K = "{ANT}history"\n')
    _git(temp_repo, "add", "leak.py")
    _git(temp_repo, "commit", "-qm", "oops")
    f.write_text("K = None\n")
    _git(temp_repo, "commit", "-qam", "remove")
    assert run("--all", cwd=temp_repo).returncode == 0
    res = run("--range", f"{base}..HEAD", cwd=temp_repo)
    assert res.returncode == 1
    assert "anthropic-key" in res.stderr


def test_all_includes_untracked_but_not_ignored(temp_repo: Path) -> None:
    (temp_repo / ".gitignore").write_text("ignored.py\n")
    (temp_repo / "ignored.py").write_text(f'K = "{ANT}ignored"\n')
    assert run("--all", cwd=temp_repo).returncode == 0
    (temp_repo / "new.py").write_text(f'K = "{ANT}untracked"\n')
    assert run("--all", cwd=temp_repo).returncode == 1


def test_rendered_ranking_mechanism_is_blocked() -> None:
    """05 O7: the filled RANKING_MECHANISM.md is a report (gitignored); only the template may be committed."""
    assert ss.check_path("docs/RANKING_MECHANISM.md", 10)
    assert ss.check_path("docs/RANKING_MECHANISM.template.md", 10) == []
