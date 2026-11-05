#!/usr/bin/env python3
"""Repo secret and data-file scanner (01 §9). Standard library only; no third-party imports.

Usage:
    secret_scan.py --all                 tracked + untracked-not-ignored files in the work tree
    secret_scan.py --staged              staged blobs (the index, not the work tree)
    secret_scan.py --range A..B          every blob added or changed by the commits in a rev range (pre-push)
    secret_scan.py PATH [PATH ...]       given files

Fails (exit 1) on:
    * Anthropic/OpenAI/GitHub/AWS/Slack/Google token patterns, private-key blocks, and passwords in URLs/DSNs;
    * high-entropy strings (>= 32 chars, > 4.5 bits/char), except in uv.lock;
    * forbidden data/secret file types (mirrors .gitignore) and any file > 1 MB.

Allow-list: a line containing the marker ``secret-scan: allow`` is skipped. In documentation (``docs/**/*.md``,
``README.md``, ``CLAUDE.md``) a token-pattern hit is ignored when it only *describes* a pattern, i.e. its key
material is shorter than a real key. Findings never print the matched secret.
"""

from __future__ import annotations

import argparse
import fnmatch
import math
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_BYTES = 1_000_000
ENTROPY_MIN_LEN = 32
ENTROPY_BITS = 4.5
ALLOW_MARKER = "secret-scan: " + "allow"
ENTROPY_EXEMPT = ("uv.lock",)
DOC_GLOBS = ("docs/*.md", "docs/**/*.md", "README.md", "CLAUDE.md")

# Built by concatenation so this file never contains a literal key prefix itself.
_ANT = "sk" + "-ant-"
_BEGIN = "-----" + "BEGIN"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    real_min_len: int = 0
    """In documentation, a hit shorter than this only describes the pattern and is ignored."""


RULES: tuple[Rule, ...] = (
    Rule("anthropic-key", re.compile(re.escape(_ANT) + r"[A-Za-z0-9_\-]*"), real_min_len=40),
    Rule("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}"), real_min_len=40),
    Rule("private-key", re.compile(_BEGIN + r" (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    Rule("password-in-url", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^:@/\s'\"]+:[^@\s'\"]+@")),
    Rule("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    Rule("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule(
        "aws-secret-key",
        re.compile(r"(?i)aws_?secret_?access_?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"),
    ),
    Rule("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    Rule("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
)

_TOKEN = re.compile(rf"[A-Za-z0-9+/=_\-]{{{ENTROPY_MIN_LEN},}}")

# Mirrors the data/secret rules in .gitignore.
FORBIDDEN_GLOBS = (
    ".env", ".env.*", "*.db", "*.sqlite", "*.sqlite3", "*.sqlite-*", "*.parquet", "*.feather", "*.pkl", "*.joblib",
    "*.jsonl.gz", "*.csv", "*.xlsx", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
)  # fmt: skip
FORBIDDEN_ALLOW = (".env.example",)
FORBIDDEN_DIRS = ("data", "reports", "logs")
FORBIDDEN_DIR_ALLOW = ("ideation/reports",)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    snippet: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.rule}{': ' + self.snippet if self.snippet else ''}"


def redact(s: str) -> str:
    return s[:6] + "…" + f"({len(s)} chars)" if len(s) > 8 else "…"


def shannon_bits(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _is_doc(path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in DOC_GLOBS)


def check_path(path: str, size: int) -> list[Finding]:
    p = PurePosixPath(path)
    out: list[Finding] = []
    if p.name not in FORBIDDEN_ALLOW and any(fnmatch.fnmatch(p.name, g) for g in FORBIDDEN_GLOBS):
        out.append(Finding(path, 0, "forbidden-file-type", ""))
    parents = [str(PurePosixPath(*p.parts[: i + 1])) for i in range(len(p.parts) - 1)]
    for parent in parents:
        if PurePosixPath(parent).name in FORBIDDEN_DIRS and parent not in FORBIDDEN_DIR_ALLOW:
            out.append(Finding(path, 0, f"forbidden-directory ({parent}/)", ""))
            break
    if size > MAX_BYTES:
        out.append(Finding(path, 0, f"file-too-large ({size:,} bytes > {MAX_BYTES:,})", ""))
    return out


def scan_text(path: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    doc = _is_doc(path)
    entropy = PurePosixPath(path).name not in ENTROPY_EXEMPT
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for rule in RULES:
            for m in rule.pattern.finditer(line):
                if doc and len(m.group(0)) < rule.real_min_len:
                    continue
                out.append(Finding(path, lineno, rule.name, redact(m.group(0))))
        if entropy:
            for m in _TOKEN.finditer(line):
                tok = m.group(0)
                if shannon_bits(tok) > ENTROPY_BITS:
                    out.append(Finding(path, lineno, "high-entropy-string", redact(tok)))
    return out


def scan_blob(path: str, data: bytes) -> list[Finding]:
    out = check_path(path, len(data))
    if b"\x00" in data[:8192]:
        return out  # binary: type/size checks only
    return out + scan_text(path, data.decode("utf-8", errors="replace"))


# ── git plumbing ─────────────────────────────────────────────────────────────────────────────────────────────
def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def _lines(raw: bytes) -> list[str]:
    return [x for x in raw.decode().split("\0") if x]


Blob = tuple[str, str, bytes]  # (repo path, where it came from, content)


def iter_all() -> Iterator[Blob]:
    for path in _lines(_git("ls-files", "-z", "--cached", "--others", "--exclude-standard")):
        try:
            with open(path, "rb") as fh:
                yield path, path, fh.read()
        except FileNotFoundError:  # deleted in the work tree but still in the index
            continue


def iter_staged() -> Iterator[Blob]:
    for path in _lines(_git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")):
        yield path, f"{path} (staged)", _git("show", f":{path}")


def iter_range(rev_range: str) -> Iterator[Blob]:
    """Every blob added or modified by any commit in the range, so a secret removed later is still caught."""
    for commit in _git("rev-list", *rev_range.split()).decode().split():
        raw = _git("diff-tree", "--no-commit-id", "--root", "-r", "-z", "--name-only", "--diff-filter=ACMR", commit)
        for path in _lines(raw):
            yield path, f"{path} (commit {commit[:8]})", _git("show", f"{commit}:{path}")


def iter_paths(paths: Iterable[str]) -> Iterator[Blob]:
    for path in paths:
        with open(path, "rb") as fh:
            yield path, path, fh.read()


def scan(items: Iterable[Blob]) -> list[Finding]:
    findings: list[Finding] = []
    for path, label, data in items:
        findings.extend(Finding(label, f.line, f.rule, f.snippet) for f in scan_blob(path, data))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="scan the work tree (tracked + untracked, not ignored)")
    mode.add_argument("--staged", action="store_true", help="scan staged blobs")
    mode.add_argument("--range", metavar="REVS", help="scan blobs changed by commits in a rev-list range")
    ap.add_argument("paths", nargs="*", help="explicit files to scan")
    args = ap.parse_args(argv)
    if args.all:
        items: Iterable[Blob] = iter_all()
    elif args.staged:
        items = iter_staged()
    elif args.range:
        items = iter_range(args.range)
    elif args.paths:
        items = iter_paths(args.paths)
    else:
        ap.error("choose --all, --staged, --range or give paths")
    findings = scan(items)
    for f in findings:
        print(f"SECRET-SCAN {f}", file=sys.stderr)
    if findings:
        print(f"secret_scan: {len(findings)} finding(s); nothing may be committed or pushed", file=sys.stderr)
        return 1
    print("secret_scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
