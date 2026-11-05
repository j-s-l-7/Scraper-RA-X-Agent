# Build status

| Phase | Status | Notes |
|---|---|---|
| Plan | done | docs/plan/00–03 |
| P0 scaffold | done (branch `phase-0-scaffold`) | see "Phase 0" below |

## Phase 0

Date 2026-11-05. Host: Intel macOS, uv 0.9.28, CPython 3.12.12. Lake: PostgreSQL 15.15 at `localhost:5433/equity_lake`.

### What was built
- **Scaffold:** `pyproject.toml` (Python `>=3.12,<3.13`, the 01 §9 dependency set as minimum pins, dev: pytest,
  pytest-xdist, hypothesis, freezegun, ruff), `uv.lock` (125 KB), `.python-version`, ruff config, pytest markers
  `unit`/`leakage`/`lake`. Markers are assigned by test directory. `lake` tests auto-skip when the lake is unreachable
  as `ideation_rw`. `.env.example`, README setup section, `CLAUDE.md`.
- **Package `ideation/`:** `cli.py` (Typer, entry point `ideation`; commands `bootstrap-role`, `init-db`, `doctor`,
  `runs list|show`), `config.py` (pydantic-settings `IDEATION_*`, `extra='ignore'`, optional `IDEATION_SECRETS_FILE`;
  YAML methodology `config/*.yaml` → `config_hash`), `logs.py` (JSON lines to `~/Ideation-data/logs/<date>.jsonl` +
  rich console), `runs.py` (registry with `run_key` idempotency), `repro.py` (frame SHA-256), `doctor.py`.
- **Lake:** `lake/adapter.py` (psycopg 3. Read-only session: `statement_timeout=120s`, `application_name=ideation-ro`,
  empty `search_path`, identity check. COPY → Arrow. `snapshot()` = REPEATABLE READ READ ONLY. Accepts only
  `NamedQuery`). `lake/queries.py`. `lake/bans.py` is the ban list shared by the adapter and the static test.
- **DB:** `db/writer.py` (`guard()` default-deny on write targets outside `ideation.*`; `GuardedConnection`),
  `db/migrate.py` (ledger + checksums + advisory lock), `db/bootstrap.py`, `db/identity.py`.
  `sql/bootstrap/000_role.sql`. `sql/ideation/001_init.sql` (01 §1.3) and `002_pit_validation.sql` (02 tables:
  `run_manifest`, `experiment_trial`, `calendar`, `security_map`, `universe_member`, `universe_coverage`, `label`).
- **PIT:** `pit/lags.py` (vendored constants from Scraper `completeness.py@a478ebb0`; `fact_available_from`,
  `match_event`, `shp_available_from`, `macro_available_from`, `fpi_sector_available_from`, `label_available_from`),
  `pit/overrides.py` (COVID), `pit/contract.py` (types + Phase-1 stubs).
- **Hygiene:** `scripts/secret_scan.py` (`--all`, `--staged`, `--range`, paths) and `.githooks/pre-push`.
  `core.hooksPath=.githooks` set locally.

### Lake changes (owner-approved, 03 §C1)
`uv run ideation bootstrap-role --yes`, run twice (second run proves idempotency):

| attribute | before run 1 | after run 1 | after run 2 |
|---|---|---|---|
| role exists / login | False / False | True / True | True / True |
| superuser / createrole / createdb | False | False | False |
| bypassrls | False | True | True |
| schema `ideation` owner | (none) | ideation_rw | ideation_rw |
| SELECT grants / lake relations | 0 / 65 | 65 / 65 | 65 / 65 |
| write grants on lake | 0 | 0 | 0 |
| default ACL schemas | () | (equity, macro) | (equity, macro) |

Runtimes 0.83 s / 0.79 s. `ideation init-db` then created schema objects: run 1 `applied: [1, 2]` (1.02 s), run 2
`applied: none; already applied: [1, 2]` (0.83 s). Nothing in `equity`/`macro` was modified.

### Acceptance (final run, all exit 0)

| command | result | runtime |
|---|---|---|
| `uv sync` (first, cold, with downloads) | 68 packages | 32.7 s |
| `uv sync --frozen` | Audited 68 packages | 0.11 s |
| `uv run python -c 'import sys; print(sys.version)'` | 3.12.12 | 0.17 s |
| `uv run ruff check .` | All checks passed! | 0.14 s |
| `uv run pytest -m "not lake"` | **267 passed**, 24 deselected (8.66 s inside pytest) | 10.1 s |
| `uv run pytest -m lake` | **24 passed**, 267 deselected (4.14 s inside pytest) | 5.4 s |
| `uv run pytest -m lake` with an unreachable DSN | 24 skipped | 1.4 s |
| `uv run python scripts/secret_scan.py --all` | secret_scan: clean | 0.37 s |
| `uv run ideation init-db` ×2 | applied: none; already applied: [1, 2] | 1.6 s / 1.0 s |
| `uv run ideation doctor` | doctor passed (0 FAIL, 1 WARN) | 1.3 s |

Test counts by marker: unit 154, leakage 113, lake 24 (291 total). Coverage of the required items:
- T-10..T-14 (`tests/leakage/test_lags.py`): exact 02 §7 dates, including leap Feb-29 (T-11), the FY-closing quarter
  (T-12), event later-wins / earlier-ignored / future-dated-ignored (T-13), SHP Sunday deadline → Monday 2024-04-22,
  CPI/IIP leap and non-leap, and FY2020 ≥ 2020-08-01 (T-14).
- Lag pins citing the source (`tests/unit/test_lag_pins.py`).
- `writer.guard` (`tests/unit/test_writer_guard.py`): 26 accepted and 60 rejected forms, plus every verb against
  `equity.`/`macro.`. The migration files pass the guard.
- C1 on the lake (`tests/lake/test_role_and_readonly.py`): `insert into equity.company` as a writable ideation_rw
  session → `InsufficientPrivilege` ("permission denied"). Writes on the RO session (to `ideation.run`, which the role
  owns, and to `equity.company`) → `ReadOnlySqlTransaction`. `count(*) equity.listing_history` > 0. App paths refuse
  the superuser DSN.
- Ban list (T-17): the runtime test raises for each of 31 banned sources, before connecting. The static AST scan of
  `ideation/{features,labels,universe,models,cv,eval,backtest}` also carries the C10 import contract and the clock
  ban, each with planted-module self-tests. A lake test proves that every view or function reading a banned table is
  itself banned.
- Secret scan: planted `sk-ant-…` in a temp file is flagged, and `--all` passes on the repo. `--staged` reads the index.
  `--range` catches a secret removed in a later commit.
- init-db: twice is idempotent (catalog fingerprint unchanged; ledger checksums match). The files applied twice to a
  throw-away schema, rolled back, give an identical catalog.
- Run registry: write/read; skip on the same `run_key`; `--force` supersedes; failure recorded.
- T-19 scaffold: SHA-256 of the score parquet is identical across runs and across `n_jobs` 1 vs -1.

`ideation doctor` on the real lake (2026-11-05): lake_identity ideation_rw (superuser off); role_attributes
bypassrls only; session_guards read_only=on, statement_timeout=2min, application_name=ideation-ro, search_path='';
read_only_enforced; no_lake_writes; lake_select 65/65; rls_bypass 16,396 rows; schema_version 2; lag_pins 45/60/21;
lake_freshness nse_bhav_full max 2026-11-05 = ^NSEI max (0 sessions behind); **WARN** scrape_runs: 3 started < 24 h
ago, 64 older rows still marked `running`; xagent_db / library_db present; secrets_file present (mode 600,
never opened); git_hooks OK.

Resolved versions: pandas 3.1.1, numpy 2.5.4, pyarrow 26.0.1, scikit-learn 1.9.1, lightgbm 4.7.0, cvxpy 1.9.3,
psycopg 3.3.8, pydantic 2.14.3, typer 0.27.3. pandas resolved to **3.x**, so later phases should expect pandas-3
semantics (copy-on-write, string dtype).

### Deviations from the plan (and why)
- `run.status` adds `superseded`. Without it, a `--force` rerun of an ok run would violate the unique
  "one ok row per run_key" index. `schema_migration` adds `name` and `checksum` (drift detection).
- 01 §1.3's unnamed partial unique indexes are now named with `if not exists`, so reruns cannot duplicate them.
  `v_latest_ideas` is concrete SQL.
- Ban list is wider than 02 T-17. It adds snapshot/bookkeeping views and tables found in the lake: `v_latest_snapshot`,
  `v_quality_*`, `dq_daily`, `dq_issue`, `completeness_daily`, `v_document_completeness`, `v_coverage`,
  `v_source_coverage` (the last two found by the dependency test), `universe_candidate`, `growth_summary`,
  `financial_fact_history`, `historical_prices_history` and `v_financials_asof`. Any SQL text containing `'TTM'` is
  refused in backtest mode (fail-closed), including negative filters.
- `fact_available_from` takes a required keyword `today` (the data observation date), so future-dated events can be
  ignored without reading the clock. `period_type` `TTM` and `H` raise (the lake has 37k `H` rows, which 02 does not
  cover).
- COVID rule = max(base, base + 30 d blanket while unverified, SEBI table deadline). The blanket alone fails T-14. The
  results table does not apply to shareholding (blanket only).
- 01's `lake/calendar.py` is not created. Seam S1 is `pit/lags.fact_available_from` (03 §C10 places lags in `pit/`).
- Snapshot-to-parquet (listed in 02's P0 row) is left to P1, per 03 §E.
- `.gitignore`: added `!ideation/reports/` (the Phase-9 package would otherwise be ignored by `reports/`), key/cert
  extensions, `.hypothesis/`.
- Doctor reports lake freshness and scraper runs as WARN, not FAIL. 64 zombie `scrape_run` rows are permanently
  `running`, and the freshness *gate* belongs to `daily` (P9).

### Open issues / risks for Phase 1+
- **Event match window vs COVID:** 02's 120-day window ends 2020-07-29 for FY2020, but the extended deadline was
  2020-07-31 (day 122). Results filed after the window fall back to the statutory rule (residual bias #6). Consider
  widening the window to `max(120, overridden deadline + slack)`, which is conservative.
- COVID override entries are unverified against SEBI circulars (P1 sign-off item). Shareholding Y-frequency rows have
  non-March period_ends (Sep 13k, others) to be handled in P3.
- `equity.scrape_run` has 64 rows stuck in `running` since 2026-11-01..03. P9's freshness gate must use a recency
  window (doctor uses 24 h), not "any running".
- The `lake` test suite needs the lake and the role; it skips cleanly otherwise.
