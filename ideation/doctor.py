"""``ideation doctor``: environment and lake health (01 §8.1).

FAIL marks a broken safety or setup invariant: identity, read-only, grants, RLS bypass, schema version, lag pins,
data dir. WARN marks a condition the user should know about that is not a setup error: lake freshness, scraper runs
still marked running, missing optional files. The freshness *gate* belongs to ``daily`` (Phase 9). Doctor only reports.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

import psycopg

from ideation.config import REPO_ROOT, Settings, is_inside_repo, load_methodology, redact_dsn
from ideation.db.migrate import check_drift, discover
from ideation.lake import queries as Q
from ideation.lake.adapter import LakeMode, LakeReader, LakeSession, check_query
from ideation.lake.bans import BannedSourceError
from ideation.pit import lags

SCRAPE_ACTIVE_HOURS = 24
LIBOMP_DIRS = (Path("/usr/local/opt/libomp"), Path("/opt/homebrew/opt/libomp"))  # Intel / Apple-silicon brew
EXPECTED_PINS = {"RESULT_LAG": 45, "YEAR_RESULT_LAG": 60, "SHP_LAG": 21}


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def _ok(name: str, detail: str) -> Check:
    return Check(name, Status.OK, detail)


def _warn(name: str, detail: str) -> Check:
    return Check(name, Status.WARN, detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name, Status.FAIL, detail)


# ── local checks ─────────────────────────────────────────────────────────────────────────────────────────────
def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) == (3, 12)
    return (_ok if ok else _fail)("python", f"{v.major}.{v.minor}.{v.micro} (requires 3.12)")


def check_config(settings: Settings) -> Check:
    try:
        m = load_methodology(settings.config_dir)
    except (OSError, ValueError) as exc:
        return _fail("config", str(exc))
    return _ok("config", f"{', '.join(m.files)}; config_hash {m.short_hash}")


def check_data_dir(settings: Settings) -> Check:
    d = settings.data_dir
    if is_inside_repo(d):
        return _fail("data_dir", f"{d} is inside the repo")
    created = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    if not os.access(d, os.W_OK):
        return _fail("data_dir", f"{d} is not writable")
    return _ok("data_dir", f"{d}{' (created)' if created else ''}")


def check_secrets_file(settings: Settings) -> Check:
    """Existence and permissions only. The file is never opened here."""
    p = settings.secrets_file
    if not p.exists():
        return _ok("secrets_file", f"{p} absent (optional)")
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode & 0o077:
        return _warn("secrets_file", f"{p} is mode {mode:o}; prefer 600")
    return _ok("secrets_file", f"{p} present, mode {mode:o}")


def check_native_libs(libomp_dirs: tuple[Path, ...] = LIBOMP_DIRS, platform: str = sys.platform) -> Check:
    """LightGBM and XGBoost wheels link libomp on macOS (05 §0.1): brew's libomp must exist and both must import."""
    found = next((d for d in libomp_dirs if (d / "lib").is_dir()), None)
    if platform == "darwin" and found is None:
        return _fail("libomp", f"libomp not found in {[str(d) for d in libomp_dirs]}: brew install libomp")
    try:
        import lightgbm
        import xgboost
    except (ImportError, OSError) as exc:
        return _fail("libomp", f"lightgbm/xgboost import failed: {exc}"[:300])
    return _ok("libomp", f"{found or 'n/a'}; lightgbm {lightgbm.__version__}, xgboost {xgboost.__version__}")


def check_lag_pins() -> Check:
    actual = {k: getattr(lags, k) for k in EXPECTED_PINS}
    ok = actual == EXPECTED_PINS
    return (_ok if ok else _fail)("lag_pins", ", ".join(f"{k}={v}" for k, v in actual.items()))


def check_ban_list() -> Check:
    try:
        check_query(Q.NamedQuery("probe", "select * from equity.market_snapshot", ()), None, LakeMode.BACKTEST)
    except BannedSourceError:
        return _ok("backtest_ban", "adapter refuses banned sources in backtest mode")
    return _fail("backtest_ban", "a banned source was not refused in backtest mode")


def check_paths(settings: Settings) -> list[Check]:
    out = []
    for name, p in (("xagent_db", settings.xagent_db), ("library_db", settings.library_db)):
        out.append(_ok(name, str(p)) if p.is_file() else _warn(name, f"{p} not found"))
    return out


def check_git_hooks() -> Check:
    try:
        path = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        path = ""
    if path == ".githooks":
        return _ok("git_hooks", "core.hooksPath=.githooks")
    return _warn("git_hooks", "run: git config core.hooksPath .githooks")


# ── lake checks ──────────────────────────────────────────────────────────────────────────────────────────────
def check_session(s: LakeSession, settings: Settings) -> list[Check]:
    user, superuser, ro_default, ro_now, timeout, app, search_path = s.fetch_one(Q.SESSION_SETTINGS)
    attrs = s.fetch_one(Q.ROLE_ATTRIBUTES)
    out = [
        (_ok if user == settings.app_role and superuser == "off" else _fail)(
            "lake_identity", f"{user} (superuser={superuser}) via {redact_dsn(settings.lake_dsn)}"
        ),
        (_ok if attrs == (False, True, False, False) else _fail)(
            "role_attributes", "superuser={}, bypassrls={}, createrole={}, createdb={}".format(*attrs)
        ),
        (_ok if (ro_default, ro_now, search_path) == ("on", "on", "") else _fail)(
            "session_guards",
            f"read_only={ro_default}, statement_timeout={timeout}, application_name={app}, search_path='{search_path}'",
        ),
    ]
    try:
        s.fetch(Q.RO_WRITE_PROBE)
        out.append(_fail("read_only_enforced", "a write succeeded on the read path"))
    except psycopg.errors.ReadOnlySqlTransaction:
        out.append(_ok("read_only_enforced", "write on the read path → ReadOnlySqlTransaction"))
    return out


def check_grants(s: LakeSession) -> list[Check]:
    writable = [r[0] for r in s.fetch(Q.WRITE_GRANTS_ON_LAKE)]
    unreadable = [r[0] for r in s.fetch(Q.UNREADABLE_LAKE_TABLES)]
    (n,) = s.fetch_one(Q.LISTING_HISTORY_COUNT)
    return [
        _fail("no_lake_writes", f"write privileges on: {writable[:5]}")
        if writable
        else _ok("no_lake_writes", "no INSERT/UPDATE/DELETE/TRUNCATE/CREATE on equity or macro"),
        _fail("lake_select", f"cannot SELECT: {unreadable[:5]}")
        if unreadable
        else _ok("lake_select", "SELECT on every equity/macro relation"),
        (_ok if n > 0 else _fail)("rls_bypass", f"equity.listing_history rows visible: {n:,}"),
    ]


def check_schema_version(s: LakeSession) -> Check:
    migrations = discover()
    try:
        rows = s.fetch(Q.SCHEMA_MIGRATIONS)
    except psycopg.errors.UndefinedTable:
        return _fail("schema_version", "ideation.schema_migration missing: run `ideation init-db`")
    applied = {int(v): str(c) for v, _name, c in rows}
    try:
        check_drift(migrations, applied)
    except RuntimeError as exc:
        return _fail("schema_version", str(exc))
    pending = [m.version for m in migrations if m.version not in applied]
    if pending:
        return _fail("schema_version", f"pending migrations {pending}: run `ideation init-db`")
    return _ok("schema_version", f"version {max(applied)} ({len(applied)} migrations, checksums match)")


def check_freshness(s: LakeSession, today: date) -> list[Check]:
    (bhav_max,) = s.fetch_one(Q.BHAV_MAX_DATE)
    nsei_max, behind = s.fetch_one(Q.NSEI_SESSIONS_AFTER, {"after": bhav_max})
    active, stale, last = s.fetch_one(Q.SCRAPE_RUNS_RUNNING, {"active_hours": SCRAPE_ACTIVE_HOURS})
    age = (today - bhav_max).days if bhav_max else None
    fresh = f"nse_bhav_full max {bhav_max} ({age}d old); ^NSEI max {nsei_max}; bhav behind by {behind} session(s)"
    runs = (
        f"{active} started < {SCRAPE_ACTIVE_HOURS}h ago (latest {last:%Y-%m-%d %H:%M}); {stale} older never closed"
        if last
        else "none running"
    )
    return [
        (_warn if behind else _ok)("lake_freshness", fresh),
        (_warn if active else _ok)("scrape_runs", runs),
    ]


def run_checks(settings: Settings, *, today: date) -> list[Check]:
    checks = [
        check_python(),
        check_config(settings),
        check_data_dir(settings),
        check_secrets_file(settings),
        check_lag_pins(),
        check_native_libs(),
        check_ban_list(),
    ]
    reader = LakeReader.from_settings(settings, LakeMode.LIVE)
    lake_steps: list[Callable[[LakeSession], list[Check]]] = [
        lambda s: check_session(s, settings),
        check_grants,
        lambda s: [check_schema_version(s)],
        lambda s: check_freshness(s, today),
    ]
    try:
        with reader.session() as s:
            for step in lake_steps:
                checks.extend(step(s))
    except (psycopg.Error, RuntimeError) as exc:
        checks.append(_fail("lake", f"{type(exc).__name__}: {exc}".strip()[:300]))
    checks.extend(check_paths(settings))
    checks.append(check_git_hooks())
    return checks
