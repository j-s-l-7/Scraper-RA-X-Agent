# CLAUDE.md: rules for agents working in this repo

Read `docs/plan/03_RECONCILED_DECISIONS.md` first (binding), then 01/02 for the phase you implement. Record every
phase in `docs/STATUS.md` (commands, runtimes, measured results).

## Hard rules
- **Never write to `equity`/`macro`** and never touch Supabase, `~/Scraper - Screener.in` or `~/X-Agent`. All output goes
  to schema `ideation`. App connections run as `ideation_rw` (SELECT-only on the lake, BYPASSRLS). The adapter and writer
  refuse any other identity, including the trust-auth superuser.
- **Lake reads only through `ideation/lake/adapter.py`** with `NamedQuery` constants from `ideation/lake/queries.py`. The
  read session is read-only, has a statement_timeout, and an empty `search_path`, so schema-qualify everything.
  Backtest mode refuses the ban list in `ideation/lake/bans.py`. A new live-only source goes on that list.
- **Writes only through `ideation/db/writer.py`** (`guard()` rejects targets outside `ideation.*`). Schema changes are
  new numbered files in `sql/ideation/` (never edit an applied one; init-db checks checksums).
- **PIT:** availability comes from `ideation/pit/lags.py` (facts: deadline + 1 day; macro: release date; labels:
  `label_end` per 03 §C3). Never call `date.today()`/`datetime.now()` in `features/labels/universe`. Feature builders
  take a `PitView`, never a connection. `tests/leakage/test_static_ban.py` enforces this.
- **No data or secrets in git:** no `.env`, `*.db`, `*.sqlite*`, `*.parquet`, `*.csv`, reports or logs. Never read
  `~/Ideation-data/.secrets.env` or any other project's `.env`. `scripts/secret_scan.py --all` must pass.
- **Determinism:** seed everything. Predict forests with `n_jobs=1` (parallel predict is not bit-reproducible).

## Commands
`uv run ruff check .` · `uv run pytest -m "not lake"` · `uv run pytest -m lake` · `uv run ideation doctor` ·
`uv run ideation init-db` · `uv run python scripts/secret_scan.py --all`
