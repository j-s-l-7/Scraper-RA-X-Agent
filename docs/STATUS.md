# Build status

| Phase | Status | Notes |
|---|---|---|
| Plan | done | docs/plan/00–03 |
| P0 scaffold | done (branch `phase-0-scaffold`) | see "Phase 0" below |
| P1 snapshot / universes / labels | done (merged to `main`) | see "Phase 1" below |
| PT1 text extraction | done (merged to `main`) | see "PT1" below |
| E1 eval stack + zoo | done, merged to `main` (synthetic only; one known gap, see E1) | see "E1" below |
| P2 fundamental features | done (merged to `main`; features, universes and labels rebuilt on `main` 2026-11-06 with the feature-completeness hook) | see "P2" below |
| P3 technical / regime / RRG / events | done (merged to `main`) | see "P3" below |
| PT2 text PIT features + vendor gate | done (merged to `main`) | see "PT2" below |
| Outcomes (05 §5, moved to MS1-text) | done on branch `phase-outcomes`, not merged | see "Outcomes" below |
| PT3 annual-report text | planned after MS3 | 05 §12 |

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
| `uv run pytest -m "not lake"` | **267 passed**, 25 deselected (8.66 s inside pytest) | 10.1 s |
| `uv run pytest -m lake` | **25 passed**, 267 deselected (~4.5 s inside pytest) | 5.5 s |
| `uv run pytest -m lake` with an unreachable DSN | all skipped | 1.4 s |
| `uv run python scripts/secret_scan.py --all` | secret_scan: clean | 0.37 s |
| `uv run ideation init-db` ×2 | applied: none; already applied: [1, 2] | 1.6 s / 1.0 s |
| `uv run ideation doctor` | doctor passed (0 FAIL, 1 WARN) | 1.3 s |

Test counts by marker: unit 154, leakage 113, lake 25 (292 total). Coverage of the required items:
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
- Macro lag dispatch: every one of the 171 real `macro.series` codes resolves to the family its source implies.

**Fresh-clone verification** (`git clone --branch phase-0-scaffold` into a scratch dir): exec bits 100755 on the
hook and the scanner, `uv lock --check` ok, `uv sync --locked` 2.0 s, ruff clean, the offline and lake suites green,
secret scan clean, `python -m ideation --help` and `ideation runs list` (empty) work, doctor passed. The first
offline run in a brand-new venv took 150 s because Python compiles the bytecode of numpy, scipy and scikit-learn on
first import. The warm rerun took 9.3 s. The pre-push hook (fed a simulated new-branch push) passed in 14 s.

CLI commands log JSON to the data dir and print their own rich tables (console log handler off).

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

## Phase 1

Date 2026-11-06. Branch `phase-1-pit-universe-labels`, not pushed. Lake observation date: 2026-11-05, the last bhav session.
Final snapshot `20261105-e33e7986`. Runs 130–132 (snapshot, universe, labels) were recorded at git `a499ccf` on a
clean tree.

### What was built
- **Migration `003_p1_universe_labels.sql`**. It was tested in the new `tests/lake/conftest.py::ddl_sandbox`
  (`BEGIN; apply pending; yield; ROLLBACK`) and is now **applied to the live schema** (`init-db`: `applied: [3]`,
  1.7 s; a second run was a no-op). It adds:
  - `universe_member`: `traded_ok`, `last_trade`, `in_u1000`/`in_u500`, `in_u_score`, `in_u_score_adv05`,
    `in_u_score_t`, `u_score_t_reason`, `has_fy_at_anchor`, `mcap_a/b1/b2_cr`, `feature_complete`. The
    exclusion-reason enum gains `thin_trading`, `adv_below_floor`, `mcap_unknown`, `mcap_below_floor_100` and
    `features_incomplete`, and `scorable = in_u_score` is now a check.
  - `universe_coverage`: `n_u_score*` and the per-reason counts.
  - `label`: `entry_price`, `exit_price`, `last_trade`, `ret_total_last_price`.
  - `security_map`: `company_link`, `series_last`.
  - New table `holdout_unseal` (05 §8).
  - `test_init_db` no longer applies pending files to the live ledger. It checks that applied versions are sorted
    and unique and start at 001; gaps are allowed for E1's 010–012.
- **Snapshot** (`ideation/lake/snapshot.py`, `ideation snapshot --full|--delta`):
  - 21 datasets in one REPEATABLE READ READ ONLY transaction, in a new `LakeMode.SNAPSHOT`. The ban list applies
    except the vendor query's own `snapshot_exempt` (`EARNINGS_VENDOR_FULL`, 05 O5); BACKTEST still refuses it.
  - Every row carries `available_from` and `role` (reference / identity / basis_conversion / data / vendor /
    crosscheck), computed after commit.
  - Facts use `lags.fact_available_from` with the first matched results event; `fy_month` comes from annual
    period_ends. H facts get `9999-12-31` (no rule).
  - The manifest records sha256 per file, lake state, git sha and config hash. `snap_id = <obs date>-<content sha8>`.
    It is content-addressed: an unchanged lake reuses the id, and `run_key` idempotency skips the run.
  - `--delta` re-pulls bhav and the yf cross-check incrementally (45-day overlap) and everything else in full.
  - Each snapshot is registered in `ideation.snapshot`, and `ideation.calendar` / `security_map` are replaced.
- **Derived** (built from the snapshot, never from the lake):
  - `lake/symbols.py`: the calendar (bhav ∪ ^NSEI, 9 bhav-missing sessions) and 02 §2.2 stitching (ISIN,
    symbol_change ±10 sessions, same-symbol contiguity ≤ 10, reuse rejected; stable crc32 security ids).
  - `lake/prices.py`: the stitched bhav series, security-level factors and `face_value_now`, with:
    - a causal factor check;
    - a residual detector for bonuses missing from the lake;
    - a jump detector for uncovered names;
    - rights flags.
- **`pit/calendar.py`**: T-23 snapping and anchor parsing. **`pit/contract.py`**: the real `PitStore`/`PitView`.
  - Availability-sorted slicing, a `_guard` on every accessor, and read-only frames (copy-on-write plus
    non-writeable arrays).
  - Per-accessor `ManifestRecord`s are written to `ideation.run_manifest`.
  - `basis_conversion_factors()` / `face_value_now()` exemptions (03 §C2).
  - The `earnings_vendor()` gate admits only `outcomes.analyst` and `features.events_vendor`. The quarterly ban
    applies before 2023-06-30.
  - `poison_after(d, mode)` (S5), implemented in Arrow: NaN / shuffle / inflate, date shifts, and fake future rows
    dated before `d`.
- **Universe** (`ideation/universe/{build,mcap,coverage,cli}.py`): U_score, the ₹0.5 cr sensitivity, U_score_T,
  in_u1000/in_u500, the per-universe reason waterfall, the 03 §C2 mcap hierarchy, and the 02 §0.1 reproduction under
  02's own definition.
- **Labels** (`ideation/labels/{build,loader,exits_report,cli}.py`):
  - Bulk TR on the stitched series (03 §C3 timing). The price leg comes from the two closes, so the bit-exact T-03
    holds.
  - Factors are counted only if available by `label_end`. Exits are classified PIT at `label_end`.
  - `ret_total_last_price` sensitivity; `excess_univ_med/cw`, `excess_n500_pr`, `xs_rank` (U_score for 1Y,
    U_score_T for 3M/1M).
  - `load_labels(anchors, horizon, purpose)` with `SealedAnchorError` and an audit list; T-26 skeleton.
  - 02 §0.2 exit-count reproduction.
- **Bias block** (`ideation/eval/bias_block.py`, `ideation bias-block`): 02 §8.2 plus the 05 §8 lines, filled from
  `ideation.*` only (no lake/psycopg import). P4/PT fields print `n/a (P4|PT)`.
- **COVID table** (`pit/overrides.py`): every entry verified against SEBI circulars and cited.
  - FY20 → 2020-07-31: SEBI/HO/CFD/CMD1/CIR/P/2020/106, 2020-06-24.
  - Q1 FY21 → 2020-09-15: …/2020/140, 2020-07-29.
  - FY21 → 2021-06-30: SEBI/HO/CFD/CMD1/P/CIR/2021/556, 2021-04-29.
  - Q3 FY20: normal.
  - Q1 FY22: no extension found; the 2021-06-30 circular is MIRSD, which covers intermediaries.
  - Reg 31 SHP Mar-2020 → 2020-05-15: …/2020/38, 2020-03-19.
  - The +30 d blanket is kept, so no date moves earlier than in Phase 0.
  - The event match window is now `max(pe + 120d, overridden deadline + 15d)`. FY2020's day-122 deadline is
    covered, and the window can only move dates later.
- **CLI**: `snapshot`, `universe build --anchors june,march,monthly`, `labels build --horizon …`,
  `coverage [--anchors]` and `bias-block` (sub-apps per 05 §10.1). `GuardedConnection.copy_rows` is a guarded COPY.

### Commands and runtimes (final, on the committed tree)

| command | result | runtime |
|---|---|---|
| `uv run ideation init-db` (003) | `applied: [3]`; rerun `applied: none; already applied: [1, 2, 3]` | 1.7 s |
| `uv run ideation snapshot --full` | `20261105-e33e7986`, 21 datasets, 487 MB incl. derived. 3,883 securities (2,618 with company), 9 bhav-missing sessions | **195.2 s** (pull + finalise 101.4 s, derived 93.3 s; wall 3:19; peak RSS ≈ 2.5 GB) |
| delta path (scratch data dir, bhav only; never `~/Ideation-data` or the DB) | merged bhav byte-identical to a fresh full pull (same sha256 ⇒ same content-addressed id, reused) | 11.6 s delta vs 43.2 s full |
| `uv run ideation universe build --anchors june,march,monthly --force` | 199 anchors (month-ends 2010-04..2026-10), 358,730 rows; `run_manifest` 3,250 hash rows | **250.4 s** (wall 4:14) |
| `uv run ideation labels build --horizon 1Y,3M,1M --anchors june,march,monthly --force` | 581 anchor×horizon, 971,740 rows (19 sealed anchors stored, not read) | **164.0 s** |
| `uv run ideation coverage --anchors june` / `uv run ideation bias-block` | tables below; block renders with code `a499ccf5b922` | < 3 s |
| `uv run ruff check .` | All checks passed! | 0.2 s |
| `uv run pytest -m "not lake"` | **325 passed, 1 skipped** (T-26 fit entry points, P4+) | 13.3 s |
| `uv run pytest -m lake` | **62 passed** (incl. 26 P1 real-snapshot acceptance tests) | 171.6 s |
| `uv run python scripts/secret_scan.py --all` | secret_scan: clean | 0.7 s |
| `uv run ideation doctor` | doctor passed; schema_version 3 (checksums match); bhav 0 sessions behind | 1.3 s |

Exit types over the unsealed anchors of all families:

| horizon | normal | corp_exit | distress | stale |
|---|---|---|---|---|
| 1Y | 283,734 | 2,967 | 716 | 1,324 |
| 3M | 296,753 | 796 | 20 | 274 |
| 1M | 297,707 | 106 | 0 | 30 |

### Acceptance

| item | result |
|---|---|
| T-01 future poison (prices / universe / mcap at 2019-06-28 inflate and 2023-06-30 NaN; labels inputs after `label_end`) | bit-identical frames and coverage (`tests/lake/test_p1_pit_lake.py`); synthetic too |
| T-03 past poison (OHLC ≤ A) | bit-identical labels (real 2019-06-28 1Y/1M and synthetic); entry = s1(A) |
| T-15 rename stitching | 400+ in-data symbol changes all resolve to one security; ZOMATO≡ETERNAL, TATAMOTORS≡TMPV, TMCV separate; covered 2016-03-31 top-1000: no distress, ≥ 99% normal 1Y exits |
| T-16 survivorship | ADVANTA, KALINDEE (2016-03-31 top-1000, corp_exit); PLETHICO, COREEDUTEC (2015-07-31 top-1000, BZ distress −100%, last-price sensitivity > −100%); all uncovered, not in U_score |
| T-20 PIT adjustment | HATHWAY/MINDACORP/RELIANCE/INFY at 2016-03-31: `close_adj = raw × Π factors(eff ≤ A)` (rel 1e-12); the 2017 RELIANCE bonus is never applied inside the 2016 view |
| T-21 mcap A vs B1 at 2019-03-29 | n = 1,091; median abs err **0.75%** (< 2%); **87.9%** within 10% (≥ 85%) |
| T-22 own 1Y TR vs yfinance adj_close (unsealed June anchors, via `load_labels(purpose='qa')`) | share > 5 pp, all names / ex-rights-flagged: 2015 0.47/0.38%, 2016 0.71/0.27%, 2017 1.23/0.91%, 2018 1.08/0.70%, 2019 1.15/0.92%, 2020 1.74/0.96%, 2021 1.28/0.27%, 2022 **1.88**/0.98%, 2023 1.45/0.88%, 2024 0.69/0.65%; early anchors 2010 1.33/1.00%, 2011 0.93/0.83%, 2012 0.80/0.71%, 2013 1.20/0.88%, 2014 0.58/0.39% (≤ 2% at every unsealed June anchor 2010–2024; the test covers all of them) |
| T-23 calendar gaps | anchor 2019-09-30 → 2019-09-19; entry after 2019-09-19 → 2019-10-01; exit on 2019-09-25 → 2019-09-19 (03 §C3 amendment) |
| T-26 skeleton | sealed sets, `SealedAnchorError` until a family is unsealed, fail-closed DB read, audit list; P4+ adds fit entry points to `FIT_ENTRY_POINTS` |
| Coverage vs 02 §0.1 (02's own definition, 02 mapping) | traded counts exact (1,526 / 1,635 / 1,879 / 2,306); top-500 / top-1000 Δpp: 2016 +0.0/+0.2, 2019 +0.2/+0.3, 2022 +0.0/+0.0, 2025 +0.0/+0.0 (stitched mapping within ±0.4 pp); ADV at rank 500/1000 within 1% |
| Exit counts vs 02 §0.2 | stopped 1,052 (1,053), renamed 355 (355), ISIN continues 382 (384), true exits 665 (664), last BZ 204 (205), last BE 309 (308), last price present 1,049 (1,050), true-exit median 1Y price ratio 0.81 (0.83): all within 0.6% |
| 05 §0.1 breadth table reproduced under 05's definition (EQ/BE/BZ, ≥ 50/63 days, today's symbol) | all 24 numbers exact at 2016/2019/2022/2025 (`universe.coverage.breadth_05`, lake test) |
| exits unit tests (synthetic) | normal + dividend TR, split-neutral, BZ distress, BE-run collapse distress (−100%, last-price sensitivity), corp exit (+ rights flag), residual stale, no trade after anchor, delayed entry, a factor known only after `label_end` ignored |

**U_score per June anchor (05 §1.1, ₹0.2 cr primary, no feature-completeness filter yet):**

| June anchor | U_score | ₹0.5 cr sensitivity | U_score_T | U1000 | U_score ∩ U1000 | 05 expectation | deviation |
|---|---|---|---|---|---|---|---|
| 2015 | 674 | 570 | 849 | 1000 | 67.4% | | |
| 2016 | 845 | 717 | 971 | 1000 | 79.9% | ≈ 700 | **+20.7%** |
| 2017 | 988 | 901 | 1,115 | 1000 | 84.3% | | |
| 2018 | 976 | 859 | 1,058 | 1000 | 85.6% | | |
| 2019 | 887 | 750 | 960 | 1000 | 87.0% | ≈ 780 | +13.7% |
| 2020 | 894 | 772 | 946 | 1000 | 88.4% | | |
| 2021 | 1,228 | 1,145 | 1,288 | 1000 | 91.0% | | |
| 2022 | 1,345 | 1,218 | 1,355 | 1000 | 92.6% | ≈ 1,250 | +7.6% |
| 2023 | 1,454 | 1,311 | 1,511 | 1000 | 94.8% | | |
| 2024 | 1,653 | 1,535 | 1,725 | 1000 | 93.6% | | |
| 2025 | 1,745 | 1,550 | 1,821 | 1000 | 92.6% | ≈ 1,650 | +5.8% |

Month-end U_score ranges per year (min..max): 2016 779..925, 2017 896..1,064, 2018 920..1,080, 2019 836..928,
2020 833..1,049, 2021 1,079..1,309, 2022 1,293..1,415, 2023 1,372..1,582, 2024 1,609..1,722, 2025 1,681..1,759. Anchors
before June 2015 score only 13–30 names, because annual fundamentals are broad only from FY2015 (02 §0.5) and FY2015
becomes available at the end of May 2015. Pre-2015 U_score_T is 494–775.

**Why 2016 is +20.7% (measured, not inferred).** At 2016-06-30, our trading rules with 05's "today's nse_symbol"
mapping give 771 names; 05 has 768. Security stitching maps **+105** more (renames and ISIN changes resolved to
today's companies), which gives 876. `too_new` removes 12 (864). FY availability plus mcap ≥ ₹100 cr remove only
**2.2%** (845), not the 5–10% 05 assumed. The gap is the stitching gain (05 itself says stitching raises the old
anchors) plus the small filter drop. The P2 completeness filter will lower N further. The same decomposition gives
+79 at 2019, +81 at 2022 and +27 at 2025 from stitching, with filter drops of 1.6% / 3.4% / 1.5%.

mcap provenance in June U_score 2015–2026: A 12,130 / B1 2,334 / B2 46; flagged (methods disagree > 25%, median used)
447. Stitching: 6,531 segments → 3,883 securities. link_method: seed 2,618, contiguity 2,084, isin 521,
symbol_change 43, own 1,265. 525 symbol-change links, 16 reuse gaps rejected. Factors: 26,253 lake rows. Applied:
dividend 19,463, split 1,020, bonus 13. Checks: 9 re-dated, 11 dropped (no jump), 51 residual jumps (missing
bonuses), 180 uncovered-name jumps; 239 rights rows flagged. One bhav row could not be assigned to a security.

### Deviations from the plan (and why)
- **Exit snapping (T-23 amended).** 03 §C3, "exit = last good session ≤ A+h", is applied over 02 §2.3/T-23's
  roll-forward for exits. A label exit on 2019-09-25 therefore lands on 2019-09-19, not 2019-10-01. Entries still
  roll forward (anchor 2019-09-19 → entry 2019-10-01), and anchors snap back (2019-09-30 → 2019-09-19).
- **Exits are classified point-in-time at `label_end`.** 02 §3.3 uses hindsight in two places, and both are
  dropped:
  - "resumes after window ⇒ stale" (later trades);
  - distress via "last trade > 20 sessions before the final listing_history last_seen" (other series' last_seen).

  `stale` now means "no trade in the final 20 sessions and neither distress nor corporate exit at `label_end`". It
  gets the same last-price-then-cash return as `corp_exit`. Distress is last series BZ, or the last 60 sessions all
  BE with a >50% fall from entry. Effect: a BZ-suspended name that resumes later is −100% here (last-price
  sensitivity column kept).
- **Holdout seal is stricter than 05 §8 for 1Y.** Every 1Y anchor ≥ 2025-03-01 is sealed (Mar-2025, Jun-2025, and
  monthly 1Y anchors whose label overlaps the holdout year). 3M is sealed 2025-07..2026-07 and 1M 2025-07..2026-09,
  as specified.
- **U_score_T applies the ₹0.2 cr ADV63 floor** ("same low floors", 05 O10). On top of that: mcap ≥ ₹100 cr where
  known, else ADV63 ≥ ₹0.5 cr.
- **Factor handling** (02 §3.2 refined by data):
  - yfinance split/bonus ex-dates often precede the NSE jump by 3–8 sessions. Factors with F ≤ 0.67 are therefore
    checked causally and re-dated (−3..+10 trades) or dropped.
  - Smaller factors (bonus 1:10 etc.) and dividends ≥ 0.80 are applied unchecked: noise hides them, and checking
    them dropped real ones.
  - A residual detector adds bonuses missing from `adj_factor` (51; e.g. BAJFINANCE 2025-06-16, where the lake has
    only the split).
  - The jump detector runs **only on uncovered names**, needs a **previous close ≥ ₹5** (tick noise), and uses
    **±10% log tolerance** on corporate-action ratios (02 said 2%). Real overnight steps were 4–5% off 1/k (HATHWAY
    0.191 vs 0.2). A detected jump also needs turnover > 2× its 20-trade median, unless the step is ≤ 0.55 or ≥ 4.
    Covered names are excluded because a 34% crash (IGL 2012-04-10, F&O, no circuit) was misread.
- **Rights stay unadjusted** (03 §C5). T-22 is gated on **all** names; the figure without rights-flagged names is
  reported as well.
- **T-16 fixtures.** No uncovered top-1000 name at 2016-03-31 has a BZ/distress exit inside the next year: BZ names
  have already left the EQ/BE liquidity ranking. So the corporate exits (ADVANTA, KALINDEE) use 2016-03-31 and the BZ
  distress cases (PLETHICO, COREEDUTEC) use the 2015-07-31 top-1000.
- **U_score N 2016 is +20.7% vs 05 §1.1, outside ±15%.** The decomposition is measured below; the main driver is the
  stitching gain.
- **COVID blanket kept.** Every table entry was verified, but the +30 d blanket remains in force across
  2019-12-31..2021-06-30, so no availability date moves earlier than in Phase 0. The event window was widened as
  described above.
- **Listed sessions:** names already trading on 2010-01-04 (the first bhav day) get a 1,000-session credit, because
  their real listing is earlier.
- **Universe rows:** every stitched EQ/BE security with at least one trade in the 63-session window (≈1.8k per
  anchor), not only the top 1,000, so every reason is countable. `scorable = in_u_score`.
- **Files changed outside the P1-owned set**, all small and needed:
  - `lake/adapter.py` (`LakeMode.SNAPSHOT`), `lake/bans.py` (`exempt=`), `db/writer.py`
    (`GuardedConnection.copy_rows`, a guarded COPY);
  - `pit/lags.py` (event window), `pit/overrides.py` (citations; SHP table), new `pit/calendar.py` (universe/labels
    may not import `lake`);
  - tests: `test_adapter_bans.py`, `test_lags.py`, `test_init_db.py`, `test_migrate_files.py` (EXPECTED_TABLES +
    holdout_unseal), `tests/lake/conftest.py` (ddl_sandbox, pit_store).

### Holdout integrity (05 §8)
`labels/loader.py` did not exist during development. Before it was written, exploratory scratch scripts printed
sealed-anchor 1Y statistics for Jun-2025:
- the full-universe median 1Y return (≈ −8.2%);
- the exit-type counts (0 distress / 15 corp_exit / 3 stale);
- the T-22 cross-check share (0.34%).

No fit, calibration, selection or threshold consumed them, and every P1 code path that reads labels now goes through
the loader with sealed anchors filtered out (T-22 test, labels-CLI QA, bias inputs). Sealed labels are built and
stored, but never read. The disclosure is recorded so that MS3 can judge whether it matters; P1 has no
model-selection step it could contaminate.

### Open issues / risks for P2/P3
- **P2 must wire the feature-completeness filter.** Pass `feature_complete` (share of non-missing core features,
  indexed by `security_id`) into `universe.build.build_universe`, then rerun `ideation universe build --force` and
  `labels build --force` (xs_rank pools depend on U_score). `universe.u_score.min_feature_complete: 0.6` is already
  in config.
- **Migration 003 is live.** E1's worktree (still at 001–002) will raise "ledger has version 3 but no file" until
  it is rebased on this branch. Merge notes:
  - `tests/lake/conftest.py` has a `ddl_sandbox` on both branches (same semantics);
  - `tests/unit/test_migrate_files.py` differs: E1 allows gaps, P1 added `holdout_unseal` to EXPECTED_TABLES.
- Features must use `PitView`. Two accessors are the only hindsight paths: `securities()` (identity: company links
  through later renames; `valid_to` clipped at A) and `basis_conversion_factors()`/`face_value_now()`.
  `listing_history.last_seen`/`is_listed_now` are not exposed through PitView; keep it that way.
- yfinance `adj_close` itself misses some 2023+ splits and bonuses (MOTILALOFS 2024, SIGMA, PARAS). T-22 outliers
  above 50 pp are yfinance's errors, not ours.
- Labels of uncovered names have no dividends (29% of 1Y June labels; 7% inside U_score: companies with no
  `adj_factor` rows). Rights issues are unadjusted (169 1Y June labels flagged).
- `security_id` is a crc32 of the first segment `(symbol, isin, first_seen)`. A back-fill that changes a first_seen
  in `listing_history` would renumber that security; collisions raise.
- The universe build takes ≈ 4 min for 199 anchors (≈ 1.2 s/anchor), close to the 5-minute budget. Monthly anchors
  dominate.
- Quarterly facts: `PitView.fundamentals('Q')` raises before 2023-06-30 (02 §1.2).
- **Sealed labels are physically present.** They sit in `ideation.label` and `outputs/<snap>/labels.parquet` and can be
  read with plain SQL or pandas. The seal holds only if every P4+ consumer reads labels through
  `labels.loader.load_labels`. A static check banning direct reads of `ideation.label` / `labels.parquet` outside the
  loader would close the gap; it is not added in P1.
- Two snapshots are on disk: `20261105-ae45cd55` (superseded, still registered in `ideation.snapshot`) and the final
  `20261105-e33e7986`. Together with outputs that is ≈ 1.1 GB. `PitStore.latest` picks the newest; the older one can
  be pruned.

## P2: fundamental features

Date 2026-11-06. Branch `phase-2-fundamentals` (from `main` 2bc597a), not pushed. Snapshot `20261105-e33e7986`. No migration
(015-019 unused). `ideation init-db` was never run from the worktree.

### What was built
- `config/features_fundamental.yaml`: 32 columns (25 `core`: 19 non-financial plus the financial extras; 2 shown-only 5Y;
  5 extended `ext`), each with `family` (VIF step), `prior`, `kind: cross_sectional`, `category`, `layouts`, `set`, `growth`, `need_fy`,
  `lines`. 25 metric aliases (`section/name`) that the registry resolves against the snapshot catalog (exactly one id each).
- `ideation/features/registry.py`: loads every `config/features_*.yaml`, validates keys/priors/layouts/lines, rejects duplicate
  features and line aliases across files, exposes `priors()`, `families()`, `growth_cols()`, `broadcast_cols()`, `names(sets, layout)`.
- `ideation/features/fundamental.py`: `build_fundamental(view, members, reg)`, a pure function of one `PitView`. Rules:
  - **Availability** is the snapshot's `available_from` (P1: `max(deadline + 1, matched event + 1)`, COVID table). The builder
    re-derives nothing, asserts `feature_available_from <= anchor` and raises `LeakageError` otherwise.
  - **Basis rule**: per (company, FY) C if any C row is available at the anchor, else S (never `has_consolidated`). Growth, 3Y
    means/vols, `ccc_chg` are NaN across a C/S switch, a layout change or a period gap other than 12 months. `avg(X)` falls
    back to `X_t` in those cases (no cross-basis arithmetic).
  - **Valuation**: `mcap_pit_cr` from the P1 hierarchy (A, B1, B2) of the same `build_universe` call; EV = mcap + Borrowings -
    Cash + NCI; non-positive EV/mcap/equity gives NaN. Never a vendor close, never price/EPS.
  - **Growth (C7)**: prior-FY validity, positive base (`eps_g1`: `max(|EPS_{t-1}|, 0.5)`), NaN for FYs ending within 12 months after
    the stitched listing date (a listing date equal to the first bhav session carries no rule), `growth_flag` for identical growth
    > 500% in consecutive FYs (06 item 3).
  - **Layouts**: `fin_layout` (Revenue and Financing Profit present), `fin_other` (sector = Financial Services otherwise),
    `core`. Non-applicable columns are NaN; `transform.na_layout_frame` marks them for R1.
  - **Coverage / c_i** (02 §1.4): `feature_complete` = share of non-missing complete-flagged core features that are applicable to
    the layout and whose `need_fy` fits the data horizon at the anchor (FYs from 2015-03-31 due by the anchor; 02 §4.1).
    `c_i = feature_complete x min(1, FYs available / 3)`. Anchors before 2015-06 have no feasible horizon (NaN, hook off).
  - `neg_ebitda`: `nd_ebitda` = cross-section P99 of the anchor's core names when EBITDA <= 0 and net debt > 0 (flag set).
- `ideation/features/transform.py`: `raw_winsorized` (per-anchor P1/P99, growth P5/P95 or `dossier_median`, NaN kept; the
  `input: raw_winsorized` columns `ebitda_ev, sp, fcf_yield` that `P1_UNIFIED` needs) and `na_layout_frame`.
- `ideation/features/build.py` + `cli.py`: `ideation features build --anchors june,march [--force]` (one `add_typer` line in
  `ideation/cli.py`). Output `IDEATION_DATA_DIR/outputs/<snap>/features_fundamental.parquet` (one row per traded, company-linked
  security and anchor; identity, the 32 feature columns, flags, `feature_complete`, `c_i`, `feature_available_from`), a
  `run` row (kind `features`) and `run_manifest` hash rows. Writes nothing else (no universe, no label table).
- **Hook** (`universe/cli.py`): `load_feature_complete` reads the features parquet and `build_all` passes `feature_complete` per
  anchor into `build_universe` (`features_incomplete`, threshold `universe.u_score.min_feature_complete: 0.6`). Anchors without
  features keep the old behaviour (no filter). The features file sha is part of the universe `run_key`, so a rebuilt features file
  forces a new universe run.

### Commands and runtimes
| command | result | runtime |
|---|---|---|
| `uv run ideation features build --anchors june,march --force` | 33 anchors (2010-2026), 45,430 rows; also prints and stores (`run.stats.coverage`, `features_coverage.parquet`) the hooked U_score coverage table below | **78.7 s** wall 84 s (first run 82.3 s); the 22 anchors of 2015-2025 are inside this, so well under 3 min |
| `uv run ruff check .` | All checks passed! | <1 s |
| `uv run pytest -m "not lake"` | **471 passed**, 1 skipped, 1 xfailed, 1 xpassed (E1 documented; +22 P2 unit +3 hook) | 161 s |
| `uv run pytest -m lake` | **86 passed** (22 of them P2: registry vs catalog, 10 golden, 6 T-01, positive control, late filer, hook, dtypes, coverage row) | 284 s (287 s wall) |
| `uv run python scripts/secret_scan.py --all` | secret_scan: clean | <1 s |

Orchestrator commands on `main` after merge (not run here: they overwrite live `ideation.universe_*` and `ideation.label`):
```
uv run ideation features build --anchors june,march --force
uv run ideation universe build --anchors june,march,monthly --force     # ~4.5 min; hook active at June+March anchors
uv run ideation labels build --horizon 1Y,3M,1M --anchors june,march,monthly --force   # ~3 min; xs_rank pools follow U_score
```
Only 1Y labels at June/March anchors change (their `xs_rank` pool shrinks by the dropped names). 3M/1M use `U_score_T`, which has no
feature filter. Optional: `features build --anchors june,march,monthly` (~2.5 s per anchor, ~9 min) puts the hook on monthly
1Y anchors too; no pre-registered trial uses them, so it was not made the default.

### Acceptance
| item | result |
|---|---|
| 01 P2 #3 derived shares vs yfinance shares | superseded by 03 §C2 (the hierarchy starts from yfinance shares, method A) and covered by P1's T-21; not re-implemented |
| 01 P2 #1 registry validation | `validate_metrics` resolves all 25 aliases to exactly one metric id on the live catalog (lake test) |
| 01 P2 #2 golden values (5 named companies, 2019-06-28 and 2024-06-28) | TCS (3365), HDFCBANK (1298, `fin_layout`), BIOCON (487), SIEMENS (3061, September FY: FY Sep-2018 / Sep-2023), IDEA (1388, loss-maker; negative equity in 2024). 10 cases x 12-17 columns match values derived by hand from raw `financial_fact` rows within 1e-9 relative (plan: 1e-6). The raw rows are pinned in the test (Profit for EPS, top line, EPS, Total Assets, `available_from` per case). Examples below |
| T-01 on fundamentals | `PitStore.poison_after(A, mode)` for `inflate`, `nan`, `shuffle` at 2019-06-28 and 2023-06-30: features (incl. mcap, `feature_complete`, `c_i`) **bit-identical**, universe rebuilt on the poisoned store too. Positive control: poisoning 900 days earlier changes the frame. Synthetic variant in `tests/unit` (3 modes) |
| later-wins availability | lake test finds a March-FY filer whose `available_from` > deadline + 1, and shows it absent the session before and present on the first session on/after; unit test with a 2019-07-15 filer |
| runtime | 76.6 s for all June+March anchors (limit 3 min) |
| sealed labels | P2 never reads labels. No `ideation.label` / `labels.parquet` access anywhere in the new code or tests |

The golden test loads snapshot `20261105-e33e7986` explicitly (skips when absent), so a later `snapshot --delta` does not turn it red; the other lake tests use the latest snapshot.

Golden examples (hand arithmetic from the raw rows; mcap is the P1 value, method A):
- TCS 2019-06-28 (FY Mar-2019, C): `roe` = Profit for EPS 31,472 / avg(equity 89,446 and 85,128 = 87,287) = **0.360558**; `ep` = 31,472 / 838,569.75 = **0.037531**; `rev_g1` = 146,463 / 123,104 - 1 = **0.189750**.
- HDFCBANK 2024-06-28 (`fin_layout`, post-merger FY): `rev_g1` = Revenue 283,649 / 170,754 - 1 = **0.661156**; `fin_margin` = -16 / 100 = **-0.16** (the merged FY has negative financing profit); `lev` = 4,030,194 / 456,396 = **8.8305**; `ebitda_ev`, `roic`, `nd_ebitda` are NaN (not applicable).
- IDEA 2024-06-28: `roe` = NaN (average equity (-104,167 and -74,359) < 0), `ep` = -0.348262, `nd_ebitda` = (243,809 - 536) / 17,072 = **14.2498**.
- SIEMENS 2019-06-28: current FY ended 2018-09-30 (available 2018-11-30).

### U_score and feature coverage per anchor (June and March, 2015-2025; U_score = after the hook)
Coverage columns are the share of U_score names (of those the feature applies to) with a non-NaN value. Regenerate with `features build` (it prints this table and stores it in `run.stats.coverage`).

| anchor | U_score before | U_score after | dropped | fin names | mean feature_complete | roe | ep | ebitda_ev | fcf_yield | roic | rev_g1 | roe_3y | eps_g3 | nd_ebitda |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2015-03-31 | 45 | 45 | 0 | 2 | n/a | 86% | 100% | 76% | 93% | 76% | 43% | 32% | 27% | 74% |
| 2015-06-30 | 674 | 666 | 8 | 72 | 0.992 | 98% | 100% | 99% | 100% | 98% | 5% | 3% | 2% | 98% |
| 2016-03-31 | 786 | 770 | 16 | 71 | 0.992 | 98% | 100% | 100% | 100% | 99% | 7% | 4% | 3% | 98% |
| 2016-06-30 | 845 | 827 | 18 | 74 | 0.970 | 98% | 100% | 100% | 100% | 99% | 86% | 4% | 3% | 99% |
| 2017-03-31 | 947 | 935 | 12 | 86 | 0.972 | 98% | 100% | 100% | 100% | 100% | 87% | 6% | 3% | 99% |
| 2017-06-30 | 988 | 921 | 67 | 86 | 0.966 | 98% | 100% | 100% | 100% | 100% | 97% | 90% | 3% | 99% |
| 2018-03-28 | 1054 | 982 | 72 | 101 | 0.968 | 98% | 100% | 100% | 100% | 100% | 96% | 91% | 4% | 99% |
| 2018-06-29 | 976 | 942 | 34 | 99 | 0.948 | 98% | 100% | 100% | 100% | 99% | 96% | 92% | 75% | 99% |
| 2019-03-29 | 928 | 893 | 35 | 102 | 0.949 | 99% | 100% | 100% | 100% | 99% | 95% | 93% | 76% | 99% |
| 2019-06-28 | 887 | 863 | 24 | 101 | 0.965 | 98% | 100% | 100% | 100% | 99% | 98% | 95% | 81% | 99% |
| 2020-03-31 | 861 | 828 | 33 | 98 | 0.966 | 98% | 100% | 100% | 100% | 99% | 98% | 95% | 82% | 99% |
| 2020-06-30 | 894 | 857 | 37 | 99 | 0.965 | 98% | 100% | 100% | 100% | 99% | 98% | 95% | 82% | 99% |
| 2021-03-31 | 1112 | 1065 | 47 | 122 | 0.964 | 98% | 100% | 100% | 100% | 99% | 96% | 95% | 84% | 98% |
| 2021-06-30 | 1228 | 1172 | 56 | 131 | 0.963 | 98% | 100% | 100% | 100% | 99% | 96% | 94% | 84% | 98% |
| 2022-03-31 | 1352 | 1299 | 53 | 143 | 0.959 | 96% | 100% | 100% | 100% | 99% | 95% | 93% | 84% | 98% |
| 2022-06-30 | 1345 | 1303 | 42 | 149 | 0.957 | 97% | 100% | 100% | 100% | 99% | 94% | 94% | 80% | 99% |
| 2023-03-31 | 1372 | 1325 | 47 | 149 | 0.955 | 97% | 100% | 100% | 100% | 99% | 93% | 94% | 79% | 98% |
| 2023-06-30 | 1454 | 1409 | 45 | 158 | 0.959 | 97% | 100% | 100% | 100% | 99% | 95% | 94% | 79% | 98% |
| 2024-03-28 | 1620 | 1550 | 70 | 175 | 0.956 | 97% | 100% | 100% | 100% | 99% | 94% | 94% | 79% | 98% |
| 2024-06-28 | 1653 | 1588 | 65 | 184 | 0.958 | 98% | 100% | 100% | 100% | 99% | 95% | 94% | 79% | 99% |
| 2025-03-28 | 1681 | 1606 | 75 | 192 | 0.953 | 98% | 100% | 100% | 100% | 99% | 93% | 94% | 77% | 99% |
| 2025-06-30 | 1745 | 1672 | 73 | 195 | 0.957 | 98% | 100% | 100% | 100% | 99% | 95% | 94% | 79% | 98% |

Reading the table: `rev_g1` is thin at 2015-06/2016-03 (FY2014 exists for only 174 companies), `roe_3y`/`roic_3y` start at the
2017 anchors, `eps_g3`/`rev_g3` (4 FYs) at 2018-06; this is the 02 §4.1 calendar, not a bug. The 2015-03 anchor has no FY yet.
The hook drops 2-8% of U_score (largest at 2017-06/2018-03, 67-72 names, when the 3Y-feature horizon first opens and young
listings cannot fill it). June U_score after the hook vs 05 §1.1 expectations: 2016 827 (700; +18%), 2019 863 (780; +11%),
2022 1,303 (1,250; +4%), 2025 1,672 (1,650; +1%). The 2016 excess is the P1 stitching gain (P1 STATUS).

### Deviations from the plan (and why)
- **Features keyed by security, built per anchor from `build_universe`'s member frame** (same stitched securities and the same mcap
  rows), not read from the universe parquet. This breaks the features <-> universe cycle (the hook needs features; features need
  mcap) and costs about 1 s per anchor of in-memory universe work.
- **ROE/ROA denominators**: `roe*` is NaN when average equity <= 0 (01 §2.3 only says NaN for ROIC invested capital <= 0); the
  sign flip of a loss over negative equity is meaningless. `roa` needs average assets > 0.
- **`avg(X)` fallback**: when the prior FY is missing, on another basis, or on another layout, the denominator is `X_t` alone.
- **No yfinance fallbacks** (01 §2.3 "Fallback" column). yf sections are ~5 years deep and secondary; the screener lines cover
  ~98% of U_score for every core feature at 2019+. A missing screener line is a missing feature.
- **`eps_g1`**: no "scale by total assets when the prior is <= 0" (02 §4.1); the `max(|EPS_{t-1}|, 0.5)` denominator of 01 §2.3 is
  used instead.
- **`rev_g3`, `sp`, `fcf_yield`, `roic*`, `opm*`, `ebitda_ev`, `nd_ebitda` are core-layout only**; financials get the shared set of
  01 §2.4 (`roa`, `roe`, `ep`, `eps_g1/g3`, `rev_g1`, `roe_3y`, `roe_vol3`) plus `bp`, `lev`, `ta_g1` (both fin layouts),
  `fin_margin`, `cost_ratio`, `dep_g1` (`fin_layout`). `dep_g1` is outside `feature_complete` (banks only).
- Extended candidates `size, cfo_np, ccc_chg, payout, capex_int` are computed and flagged `set: ext` (the `core_ext` variant);
  `promoter_chg_4q` belongs to P3. `eps_g5`/`rev_g5` are computed and flagged `set: shown`.
- The feature table is a parquet only; no DB table (the `feature_set` DDL from 001 is untouched).

### Risks for P3 / MS1
- **Live-state footprint of this branch.** Three `ideation features build` runs from the worktree are in the live `ideation.run` /
  `run_manifest`, and `features_fundamental.parquet` + `features_coverage.parquet` sit in the live `outputs/20261105-e33e7986/`
  (new files only; universe and label tables untouched). The orchestrator should run `features build --force` on `main` first anyway.
- **Registry is multi-file safe.** Only `family, prior, kind, category, set` are required; `layouts`, `growth`, `need_fy`, `lines`
  default. `build_fundamental` only touches features whose `source` is `features_fundamental.yaml`, so P3/PT2 files add no columns
  to the output and do not dilute `feature_complete` (unit test). Conflicting `cleaning:` keys across files raise.
- `anchor`, `fy_end`, `feature_available_from` are python dates (date32), identical to `universe_member` / `labels`, so
  `(anchor, security_id)` merges work without casts.
- **U_score changes when the orchestrator reruns `universe build --force`.** Labels `xs_rank` (1Y June/March) must be rebuilt after;
  until then the stored labels pool the old U_score. The universe `run_key` now includes the features file sha, so a stale
  universe run cannot silently pass as current.
- **`fin_margin`/`cost_ratio`/`dep_g1` exist for only ~30-100 names a year**; R1's `na_layout_` flags carry the rest.
  MS1 must pass `na_layout_frame(df, reg)` to `xs_transform`.
- **`isna_` flags**: a name missing `roe` because of negative equity is indistinguishable from a missing line. Fine for RF, but a
  linear model sees both as 0 plus `isna_roe = 1`.
- **Sector/industry labels are today's** (layout `fin_other` and the industry z-scores depend on them): the disclosed residual bias.
- **`nd_ebitda` P99 fill** is the 99th percentile of the anchor's fresh `core`-layout names (all traded, company-linked, not only
  U_score); deterministic and PIT but not the U_score population.
- **Winsorising raw columns for `P1_UNIFIED`** must be done on the training rows by the caller per anchor (`raw_winsorized`).
- **HDFCBANK 2024 shows the merger FY** (`rev_g1` +66%, `ta_g1` +59%): real data, not cleaned. Growth winsorization handles it.
- The listing-date rule uses the stitched `valid_from`; a company whose first bhav row is later than its real listing (suspension,
  re-listing) is treated as an IPO for 12 months.

## E1: evaluation stack, model zoo, ranking mechanism, trials registry

Date 2026-11-06. Branch `phase-e1-eval-zoo` (worktree `../wt-e1`, from main f2d1feb). Synthetic data only; no lake
reads except the rolled-back DDL sandbox. `ideation init-db` was **not** run (only main applies migrations).

### What was built
- **cv/**: `walkforward.py` (annual folds: train = the 3 most recent anchors with `label_end ≤ T` ⇒ {T−3, T−2, T−1};
  monthly purged rolling folds: 36 usable anchors, 12-anchor test blocks, `embargo` gap; expanding / `train_start`;
  `walk_forward_predict` driver with a row-level C3 re-check that raises `LeakageError`; `check_available` PIT guard),
  `cpcv.py` (contiguous groups, purge = label-span overlap, embargo = first k non-purged anchors after each test group,
  backtest paths), `cscv.py` (CSCV row splits for PBO).
- **eval/**: `metrics.py` (rank IC, ICs by anchor, R-A bucket table P99…P1, spreads, hit rate, top-vs-bench,
  turnover, cost-adjusted = r − 2·bps·turnover, monotonicity, tail order), `inference.py` (NW lags/SE/t, IC summary
  with iid or stationary bootstrap CIs, sign test and classic t for n < 10, within-(anchor, industry) permutation
  null), `bootstrap.py` (iid, Politis–Romano stationary, paired Δ with P(Δ>0), over-names), `dsr.py`, `pbo.py`,
  `selection.py` (90/10 reading (b): clusters 1−|ρ_s| average linkage cut 0.5, clustered permutation importance,
  stability, 0.9·z(IC)+0.1·z(stability)), `consensus_math.py` (votes, C_maj/C_unan/B_maj, too-few flag, Kendall W
  tie-corrected, pairwise Spearman/Jaccard, ENS_RANKAVG, size-matched top-k, random-|C| percentile, set excess).
- **models/**: `zoo.py` (`LEARNERS`, `FIXED_PARAMS`, `make_learner` refuses overrides of fixed hyperparameters; uniform
  `fit(X, y, meta)` / `predict(X, meta)`; ConvergenceWarnings recorded in `warnings_`; 90/10 reading (a)
  `NinetyTenSec`), `select_vif.py` (CR1 anchor-clustered univariate OLS with t(G−1) p, best |t| per family, iterative
  VIF < 5), `phase1.py` (Unified faithful + XS, Composite dossier, Composite EW, industry equations WF + in-sample
  table, WFO selected scr with "empty" folds, Final_WFO/PostCOVID).
- **rank/**: `transform.py` (R1 winsor/dossier_median, global rank-gauss, industry z with sign flips and fallback
  group→sector→all, hybrid = global ⊕ `ind_` columns, `isna_`/`na_layout_` flags, 0 fill), `percentile.py`,
  `buckets.py`, `calibrate.py` (nested: counted anchors < T with `label_end ≤ T`, never sealed; 0.5 shrink; weighted
  isotonic; interpolation at decile mids), `conformal.py` (Mondrian by decile, signed equal-tailed 80% band, Barber
  et al. weighted quantile, half-life 2 anchors), `conviction.py`.
- **trials/**: `registry.py` (YAML loader + validation, resolved config incl. zoo params hashed with sha256 of
  canonical JSON, idempotent insert as `registered`, `family_n`, `assert_registered` → `UnregisteredTrialError`),
  `cli.py` (`ideation trials register|list|check --family …`, `register --dry-run` needs no lake).
  `config/trials_1y.yaml` (N = 50) and `config/trials_tactical.yaml` (N = 25), owned by MS1/MS2 from now on.
- **sql/ideation** `010_oos_prediction.sql`, `011_rank_calibration.sql`, `012_outcome.sql` (05 DDL plus not-null keys,
  checks on horizon/scheme/bucket/kind/p, FKs). `tests/lake/conftest.py::ddl_sandbox`.
- `xgboost 3.4.1` in pyproject + uv.lock; doctor `libomp` check (also imports lightgbm/xgboost);
  `docs/RANKING_MECHANISM.md` gitignored and blocked by the secret scan.
- Static tests: `rank` on BAN_SCANNED; cv/eval/models/rank/trials may not import `ideation.lake`/psycopg;
  features/models/universe/rank/cv may not import `ideation.outcomes` (05 §5); planted-module self-tests.
- P0 test changes: `test_init_db_twice_is_idempotent` now runs inside a rolled-back transaction (it used to call
  `migrate()` on the real schema, which from a worktree would have written versions 10–12 into the real ledger);
  migration discovery test allows version gaps; lake doctor test tolerates "pending migrations" (branch worktrees);
  methodology-hash test copies every `config/*.yaml`.

### Zoo (05 §3.2, fixed a priori; 1Y / tactical)
| key | implementation | 1Y | tactical |
|---|---|---|---|
| ols | LinearRegression on X incl. flags | — | — |
| ridge | Ridge | α=10 | α=100 |
| scr | X × prior sign, no-prior features and flags dropped, Ridge(positive=True) | α=10 | α=100 |
| pridge | PolynomialFeatures(2, no bias) on value columns + flags linear, Ridge | α=100 | α=1000 |
| rf | RandomForest(500, min_samples_leaf 25, max_features 0.33, seed 0); fit n_jobs 4, predict n_jobs 1 | as stated | 400 trees, leaf 100 |
| lgbm | LGBM(num_leaves 15, lr 0.03, 400 rounds, feature_fraction 0.7, bagging 0.8/1, min_data_in_leaf 40, deterministic, force_row_wise, 4 threads, seed 0) | as stated | min_data_in_leaf 200 |
| xgb | XGB(max_depth 4, lr 0.03, 400, subsample 0.8, colsample 0.7, min_child_weight 20, hist, 4 threads, seed 0) | as stated | min_child_weight 100 |
| mlp | MLP((32,16), alpha 1e-3, lr 1e-3, batch 256, early_stopping=False) mean of seeds 0–4 | 50 epochs | 30 epochs |
| svr | SVR(rbf, C 1, ε 0.1, gamma scale) on an anchor-stratified seeded subsample; bit-reproducible RBF predict | ≤ 6,000 rows | ≤ 8,000 rows |

LightGBM/XGBoost use the sklearn names of the spec parameters (feature_fraction = colsample_bytree, bagging_fraction =
subsample, bagging_freq = subsample_freq, min_data_in_leaf = min_child_samples; nthread = n_jobs, seed = random_state).

### Acceptance (synthetic)
| item | result |
|---|---|
| T-04 shuffled labels, all 9 learners (43 anchors × 100 names × 6 features; one WF fold: train 3 anchors, test 40; seeds 0–19) | pass. Mean OOS IC per learner between −0.0037 (scr) and +0.0019 (svr); seeds with \|t\| ≥ 2.5: **0/20 for every learner**; max \|t\| 1.77 (rf) … 2.28 (mlp). For reference the rule's own false-alarm rate is 4.4% per learner (t39), 33% that at least one of 9 trips. |
| T-05 planted Spearman 0.10 (12 anchors × 800 names × 12 features; annual WF with refits, 9 test years; seed 0; oracle IC of f0 on the test anchors 0.099) | ridge 0.087 [CI90 0.057, 0.111], scr 0.093 [0.063, 0.118], rf 0.065 [0.042, 0.084] pass [0.06, 0.14] with CI > 0 (and on seeds 1–2). **Not green for the boosters:** xgb 0.061 [0.043, 0.077] clears the floor only at this registered seed (0.044/0.054 on seeds 1/2; non-strict xfail); **lgbm 0.041 [0.024, 0.057] fails the 0.06 floor** (strict xfail). For both, CI > 0 and mean < 0.14 hold and are asserted. |
| T-05 seed spread (seeds 0/1/2, same size; oracle 0.099/0.091/0.108) | ridge 0.087/0.086/0.089, scr 0.093/0.085/0.100, rf 0.065/0.069/0.080, xgb 0.061/0.044/0.054, lgbm 0.041/0.027/0.053. At 3 × 2,000 training rows (6 anchors × 2,000, oracle 0.081/0.096/0.106): ridge 0.071/0.081/0.097, lgbm 0.028/0.045/0.069, xgb 0.036/0.032/0.079. At Spearman 0.30 the boosters reach 0.79–0.84 of the oracle (lgbm 0.231, xgb 0.246 vs ridge 0.288, oracle 0.294): weak-signal attenuation, not a pipeline defect. |
| T-06 planted leak | `check_available` raises `LeakageError` naming `x_leak` (or drops it); guarded OOS IC < 0.14; the same model with the leak included scores > 0.5 (the test has teeth). The fold driver also raises on any training row with `label_end > T`. |
| T-07 (C3) | test 2022 ⇒ train {2019, 2020, 2021}; a 2021 label ending after T purges 2021 ⇒ {2018, 2019, 2020}. |
| T-08 (C3 analog) | 1Y labels on monthly anchors, test block from 2022-01-31: 2021-01-29 (label_end 2022-01-28) kept, 2021-02-26 purged, 36 train anchors 2018-02-28 … 2021-01-29, 11 purged. Tactical 3M: last train 2019-10-31 without embargo, 2019-07-31 with embargo 3; 12 folds 2014-01 … 2025-06. |
| T-09 (O4) | 9 groups / k = 2: 36 splits, 8 paths, every path covers each anchor once, each group tested 8 times, no train anchor right after a test group, no overlapping label spans. Tactical 12 groups: 66 splits, 11 paths. |
| T-18 | the R1 fit is called once per anchor (7 calls, one anchor each); every model fit (90/10 σ, VIF selection, scr) sees exactly its fold's train anchors and only training labels. |
| T-19 | score parquet sha256 identical across two runs for all 9 learners plus Unified, Composite EW, industry WF, WFO-scr and RF 90/10; RF fit n_jobs 1 vs −1 identical. The 14 score hashes are also identical across two separate processes with different `PYTHONHASHSEED` (1 and 987). libsvm's own predict differs by ~5e-15 between identical calls, so `svr` predicts with an explicit-order RBF decision function (matches libsvm to < 1e-10, bit-identical across input alignments). |
| scr signs | all coefficients ≥ 0 after the flip; priors contradicting the data give 0. |
| VIF | planted twin (`a + 0.05·noise`, VIF > 100) dropped; remaining VIF < 5; exact collinearity ⇒ inf. |
| isotonic | raw decile α non-monotone ⇒ α_iso and α(p) monotone; α_shrunk = 0.5·α_raw. |
| conformal coverage (iid, 6 nested test anchors × 2,000 names) | 0.814, 0.791, 0.792, 0.801, 0.805, 0.808; mean **0.802** (target [0.75, 0.85]). |
| DSR worked example (Bailey & López de Prado 2014: SR 2.5 ann., N = 100, V = 0.5 ann., T = 1,250, skew −3, kurt 10) | SR0 = 0.11317 (published 0.1132), **DSR = 0.90040** (published 0.9004). |
| PBO | No deterministic published PBO number was available offline, so this is **hand-verified**, not matched to a published example: a 4 × 3 matrix with S = 4 worked by hand gives logits (−ln3, ln3, ln3, ln3, ln3, −ln3) and PBO = 1/3, reproduced exactly; zero-sum ⇒ 1, dominant ⇒ 0, noise ⇒ ≈ 0.5. |
| MLP early_stopping | `early_stopping=True` raises; fitted members have it False; overriding via `make_learner` refused. |

### Commands
| command | result | runtime |
|---|---|---|
| `uv run ruff check .` | All checks passed! | 0.2 s |
| `uv run pytest -m "not lake"` | **388 passed, 1 xfailed, 1 xpassed** (unit 225, leakage 165; the xpass is the non-strict xgb full-criterion test at seed 0) | 95 s warm (96 s wall) |
| `uv run pytest -m lake` | **27 passed** at 0b53816 (incl. ddl_sandbox DDL + registry), 7–9.5 s. Since 2026-11-06 05:30 IST the real ledger holds P1's version 3 (`p1_universe_labels`, applied from P1's branch) while main has no 003 file, so in this worktree 4 lake tests now stop at "ledger has version 3 but no file" (the hazard below). With P1's 003 file copied in temporarily (checksum matches the ledger; removed again, not committed) 26 pass; the 27th only lacks P1's `holdout_unseal` in EXPECTED_TABLES, a merge-time union. | |
| `uv run python scripts/secret_scan.py --all` | clean | 0.4 s |
| `uv run ideation trials register --family 1Y-june --dry-run` | 50 trials with hashes | ~2 s |
| `uv run ideation doctor` (worktree) | libomp OK; schema_version FAIL "pending migrations [10, 11, 12]", expected until main runs init-db | |

The real ledger was checked before and after the lake suite: versions [1, 2] only.

### Broadcast columns (follow-up fix, 2026-11-06)
Before the fix, R1 turned any column that is constant within an anchor (regime, macro, breadth) into all zeros, so
#33 RF_GLOBAL_EXT, #50 RF_GLOBAL_EXT_9010SEC and tactical #11–13 `*_T0_REGIME` (and the §11 2×2 ablation) would
have carried no regime information.
- Declaration is explicit, never inferred from values: `TransformSpec.broadcast_cols`, normally
  `rank.transform.columns_of_kind(feature_yaml)` over entries with `kind: broadcast` (P3 declares its regime/macro
  columns this way; the default kind is `cross_sectional`).
- R1 passes a declared broadcast column through raw (no winsorizing, rank or industry z; no `ind_` copy in hybrid),
  with the usual `isna_`/`na_layout_` flags. A declared column that varies within an anchor raises `ValueError`.
- `models.zoo.BroadcastScaler` (or `make_learner(key, broadcast=cols)`) standardises those columns inside `fit` with
  the mean and SD (ddof 1) of one value per training anchor, applies them unchanged to test anchors, and sets missing
  values to 0 (the training mean). Fewer than two non-missing training anchors or SD 0 ⇒ the column is 0.
- Learners: trees and ols/ridge use the standardised column like any other; pridge also forms its squares and
  interactions with features; **scr drops it** (no sign prior); Phase-I composites ignore it unless weighted.
  `Learner.fit` refuses any NaN in X (R1 fills every cross-sectional column), so an unscaled broadcast column cannot
  slip into a learner silently (Unified, which takes raw columns, is exempt).
- An undeclared cross-sectional column that is constant at an anchor is still zeroed, and `xs_transform` emits one
  `ConstantCrossSectionWarning` with the count of such (anchor, feature) pairs.
- Found on the way: `_zscore` of a constant float column (e.g. 0.7) returned ±1 because the float SD was a rounding
  residue (~1e-17), not 0. Constant columns now give exactly 0 (regression test added).
- Tests (`tests/leakage/test_broadcast.py`): with ret = 0.35·sign(regime)·f0 + noise and alternating regime signs
  (24 anchors × 300 names, train 12, test 12), RF recovers mean OOS IC **0.281** (min 0.223) with the broadcast
  path and **0.009** on the old zeroing path (which also raises the warning); lgbm 0.272 vs 0.029, pridge 0.297 vs
  0.011, ridge 0.034 both ways (no interaction term). T-18: the scaler is fitted once per fold on exactly the train
  anchors; poisoning test-anchor regime values by ×1e6 leaves mean/SD equal to the per-anchor (not per-row) train
  statistics. Also: undeclared ⇒ zeroed with a 24-pair warning; declared but varying ⇒ error; missing ⇒ flag + 0;
  unscaled NaN refused; scr ignores the broadcast column; `columns_of_kind`.

### Deviations from 05 and why
- **T-05 for lgbm fails the 0.06 floor** at the spec size (above). Hyperparameters are fixed by 05 §3.2, so nothing was
  tuned, re-seeded or resized to pass. The finding for MS1: at 1Y training sizes the booster configs cut a weak
  (Spearman 0.10) signal by roughly 40–60% (xgb is marginal: 0.061 on seed 0, 0.044/0.054 on seeds 1–2). Any a-priori
  revision must happen before `trials register` freezes the hashes; that is the orchestrator's / owner's call.
- T-04 uses one walk-forward fold with 40 test anchors per seed (near-normal t) instead of annual refits, for runtime.
- Forward-WF embargo (tactical) is implemented as a gap of `embargo` anchors after the C3 purge; CPCV embargo starts
  where the purge ends. Calibration history additionally requires `label_end ≤ T` (C3), which only matters monthly.
- Conformal band is signed and equal-tailed (10%/90%) rather than |residual| (02 §6.3), because return residuals are
  skewed; coverage target unchanged.
- Conviction: FIN notches down (05 R7) and is then capped at Medium (03 §A8), so an X-Agent notch cannot lift it.
- Stability (90/10 b) and σ (90/10 a, ddof 1) formulas are our documented decisions (sources undefined, 06 §4.5).
- P1_UNIFIED (faithful) needs the winsorized raw yield columns (trial key `input: raw_winsorized`); on the R1
  matrix it would equal P1_UNIFIED_XS.
- Migrations 013–014 unused. No `holdout` CLI (the loader and `holdout_unseal` table are P1's).

### Risks for MS1 / MS2
- Booster attenuation (above); expect lgbm/xgb to lose to ridge/scr/rf on weak signals.
- ~~R1 erases anchor-constant (broadcast) columns~~ **resolved** (see "Broadcast columns" below).
- **Ledger hazard:** until P1 takes the rolled-back `test_init_db_twice_is_idempotent`, one `pytest -m lake` run in
  P1's checkout commits 003–009 to the real ledger; this worktree's lake tests and doctor would then fail with "ledger
  has version 3 but no file".
- The MS1 card says `trials register` gives 48 rows; with the 05 §11 additions it gives **50** (1Y) and **25**
  (tactical), as intended.
- With 3 training anchors the anchor-clustered p-value uses t(2) (critical 4.30), so P1_WFO folds may often be
  "empty" (score NaN); that is the faithful rule and must be reported, not relaxed.
- `train` must call `trials.registry.assert_registered` (or `ideation trials check`) before any fit; MS1 wires it.
- Merge points: `tests/lake/conftest.py` (P1 may add the same fixture), `tests/unit/test_migrate_files.py`
  (EXPECTED_TABLES), `tests/lake/test_init_db.py`, `tests/leakage/test_static_ban.py`; P1's `eval/bias_block.py` must
  not import `ideation.lake`/psycopg under the new contract.
- Runtime: `pytest -m "not lake"` is 85 s warm; a brand-new venv adds bytecode compilation (P0 measured 150 s cold for
  the 267-test suite), so a cold first run may approach 3 min.
- Feature names in `phase1.DOSSIER_COLUMNS` / `UNIFIED_COLS` are the 05 §3.4 names; P2 must use them or MS1 maps them
  in the trial config.

## PT1: text extraction (`ideation/text/`, branch `phase-pt1-text-extraction`)

Files: `ideation/text/{reader,clean,lexicon,extract,cli}.py`; `DOCUMENTS_CATALOG` appended to `ideation/lake/queries.py`;
setting `IDEATION_DOCUMENTS_ROOT` (`Settings.documents_root`, default `~/Scraper - Screener.in`); sub-app `text` registered
by one `add_typer` line. Tests: `tests/unit/test_text_extract.py` (21), `tests/leakage/test_text_static.py` (15).
No migration (020-024 unused): the output is parquet under `IDEATION_DATA_DIR/text/` (`doc_features_LEX_V1.parquet`,
cache in `text/cache/`), nothing is written to the lake, no LLM/network, `.secrets.env` untouched.

### Commands and runtimes
- `uv run ideation text extract --types concall_transcript,concall_ppt` cold (`--no-cache`): **272 s** (4 workers,
  53,892 docs); warm (all from cache): **12.8 s**. Targets 20 min / 1 min.
- `uv run ruff check .` clean; `pytest -m "not lake"` 481 passed + 1 skipped + 1 xfail + 1 xpass in 183 s (before the final catalog test; PT1 files 36 tests pass); `pytest -m lake`
  64 passed; `scripts/secret_scan.py --all` clean.

### Measured acceptance
| Item | Target | Measured |
|---|---|---|
| transcripts after dedupe | 22,135 ± 50 | **22,335** (+200, see below) |
| PPTs after dedupe | 31,584 ± 50 | **31,614** (+30, in range) |
| `filed_date` parse, BSE transcripts | >= 80% | **99.5%** (n = 17,769); all sources 97.5%; PPT 84.3% |
| `qa_ok` transcripts | printed | **96.1%** (marker found 97.2%); PPT 0.2% |
| TCS Oct-2024 `filed_date` | 2024-10-14 | **2024-10-14** (available_from 2024-11-01) |
| 2020-Q1 mean `d_tone` | negative | **-0.133** (n = 213, 73.7% of calls negative); 2020-Q1 mean tone_net 0.357 vs 0.54-0.60 in 2018-19 and 2021-22 |

Transcript count: the lake grew after the 05 §0.1 probe (status ok transcripts 22,146 then, 22,346 now; 134 were
extracted on 2026-11-06 alone). Dedupe dropped 4 transcripts + 16 PPTs here (probe: 11 + 8). 1 document file unreadable.

Per-year coverage (docs / companies, after dedupe, readable): transcripts 2016 483/188, 2017 641/247, 2018 698/269,
2019 1006/335, 2020 1133/381, 2021 1515/522, 2022 2347/781, 2023 3144/944, 2024 3636/1069, 2025 4091/1228,
2026 3591/1304. PPTs 2016 788/339, 2017 1224/460, 2018 1018/449, 2019 1960/603, 2020 2048/640, 2021 2666/797,
2022 3281/951, 2023 3978/1111, 2024 4608/1259, 2025 5149/1426, 2026 4741/1490. BSE-PPT `filed_date` rate rises from
28-37% (2015-17) to 97% (2024+): older PPT decks mostly lack a cover letter. The CLI prints the full table and the
per-quarter tone table (mean tone_net, mean d_tone, share negative, n) on every run.

### Rules as implemented (and deviations from 05 §7.1)
- Cover letter: page 1 (and page 2 if page 1 went and it still matches) dropped; a page with a `Moderator:` line is never
  cover. `filed_date`: first date on pages 1-2 inside [doc_date - 45 d, doc_date + 75 d]; dates introduced by
  ended/period/quarter/meeting/held on/as on... are skipped (otherwise the quarter-end or board-meeting date could
  win, an early-availability leak). Extra formats beyond the spec: `14th October 2024`, `dd/mm/yyyy`.
- **Q&A split deviation:** first marker whose preceding cleaned prepared section has >= 500 tokens; a first marker in
  the moderator's introduction ("a question-and-answer session will follow") made qa_ok 66.7% with the plain
  first-match rule, 96.1% with this one. If no marker qualifies the first is kept (qa_ok = 0).
- Negation: not/no/never plus without/neither/nor/none/cannot/hardly and any `*n't` within 3 tokens, same sentence.
  Flips pos<->neg and g_up<->g_down (symmetric), cancels capex, leaves unc/lit alone.
- Lexicon LEX_V1: 534 terms (135 pos, 158 neg, 55 unc, 37 lit, 150 phrases), own wording, hash in the cache key.
- Numeric guidance: spec regex plus decimals and "per cent/percent"; guidance verb in the same sentence; per-doc median
  midpoint `guide_pct_mid`, count `n_guide_pct`; prepared remarks only when a Q&A split exists. Present in 9.0% of
  transcripts and 23.9% of PPTs.
- `available_from` (05 §7.2) is computed here with `pit/lags.py` (`match_event`, `fact_available_from`): month floor,
  filed + 1, matched event + 1, statutory floor for non-bse/nse sources. Fiscal-year month comes from the latest
  snapshot's `financial_fact` if present (else 3). `today` for event matching = latest reported event date (no clock).
- Dedupe key (company_id, doc_type, doc_date); the cache key is (pdf_sha256, doc_date, lexicon hash, extractor
  version), so one PDF filed under two company_ids is extracted once and shared.
- Read-only guarantee: `reader.py` is the only code opening Scraper files (`gzip.open(..., 'rt')`, path must sit under
  `data/documents`, `..`/absolute/symlink escapes refused). The static test blocks write calls anywhere in
  `ideation/text`, allows `to_parquet`/`mkdir` in `extract.py` only, and forbids psycopg, `ideation.db`, network/LLM
  imports, clock reads and foreign paths; `ideation.lake` is imported by `cli.py` only.

### Risks for PT2
- `filed_date <= q_end` for 0.7% of parsed docs (a date other than the filing was picked); availability is still floored
  by the month rule, but PT2 should treat `filed_src` as quality metadata.
- Month-granular `doc_date`: a call filed late in month m is available only from m+1 start (05 §7.2) at best; BSE
  `filed_date` is the only intra-month information.
- Covered-names selection: coverage is survivors only and rises from ~190 companies (2016) to 1,300 (2026).
- Tone is positive-skewed (mean tone_net ~0.6); PT2 uses within-company deltas, which the 2020-Q1 check supports.
- Non-BSE transcripts (about 4,500) carry statutory floors only, which can be later than the true filing but not earlier.
- `qa_ok` PPTs are ~0%; `qa_*` features are transcript-only.

## PT2: text PIT features + vendor-surprise gate (branch `phase-pt2-text-features`)

Date 2026-11-11. Snapshot `20261105-e33e7986`, PT1 output `doc_features_LEX_V1.parquet` (53,949 docs). No migration (020-024
unused); `init-db` never run from the worktree. The `outcomes/` module is NOT built here (moved to MS1-text, 05 §10.2.c).

### What was built
- `PitStore(text_dir=...)` serves PT1's parquet as dataset `text_docs` (unreadable docs dropped, `role='data'` added,
  dates cast), so `poison_after` and the `available_from <= anchor` guard apply to it; accessor
  `PitView.text_docs(doc_type, columns)`. `poison_after` passes `text_dir` through.
- `ideation/features/text.py` + `config/features_text.yaml` (11 registered features, set `text`, `complete: false`, so they
  never enter `feature_complete` or the U_score hook): `d_tone, d_guide, d_unc, tone_lvl, guide_lvl, gdn_r, capex_r, qa_tone,
  qa_minus_prep, ppt_d_guide, ppt_capex_r`; flags `isna_text, isna_text_prev, isna_ppt`; provenance dates; `<f>_rg`.
  Rules: visibility = `available_from` only (PT1's conservative 05 §7.2 value, not re-derived, asserted again in the builder);
  `doc_date` (month-granular) only builds windows; cur = latest visible transcript with doc_date >= A-200 d; prev = latest
  visible transcript with doc_date in [cur-400, cur-60]; PPTs same; no fill anywhere.
- `ideation/features/events_vendor.py` + `config/features_vendor.yaml`: `f_vsurp_last, f_vbeat_4q, isna_vsurp`; refuses to run
  unless `allow_vendor_estimates=True`; reads `PitView.earnings_vendor()` (caller gate unchanged); surprise recomputed
  `100*(rep-est)/|est|` (est 0 -> NaN, winsor +-100); the only `BAN_ALLOWLIST` entry. `validity_gate()` = the 05 §6.3 gate.
- CLI (registered onto the `text` sub-app from `ideation/features/text_cli.py`, because `ideation/text` may not import the DB
  layer): `ideation text features [--anchors june,march,monthly] [--force]` and `ideation text vendor-gate`.
  Outputs under `outputs/<snap>/`: `features_text.parquet`, `features_text_coverage.parquet`, `vendor_gate.json`; run row kind
  `features_text` + `run_manifest` rows (key includes docs and universe file shas).
- `registry.SETS` gains `text` and `vendor`. Callers that iterate `reg.names()` must pass `source=` (two P2 unit tests updated).
- Static tests: `BAN_ALLOWLIST = {ideation/features/events_vendor.py}` plus tests that the list is exactly that, the module really
  names the vendor columns, still obeys import/clock rules, and that a planted vendor literal elsewhere fails.

## P3: technical, ownership, regime, RRG, events

Date 2026-11-11. Branch `phase-3-technical-regime` (from `main` 7f21625's parent line), pushed per chunk, not merged. Snapshot
`20261105-e33e7986`. No migration (025-029 unused: `regime_snapshot` / `sector_rotation` already exist in 001).
`ideation init-db` was never run from the worktree.

### What was built
- `ideation/regime/`: `context.py` (one panel read per anchor, daily returns, liquidity-screened EW group / sector / market
  composites with group -> sector -> market fallback), `rrg.py` (JdK RS-Ratio / RS-Momentum on anchor-aligned weekly points,
  `variant="dossier"` 60-week ROC, quadrants), `rotation.py` (group, sector and official-index RRG tables; indices with
  < 400 sessions of history are excluded), `macro.py` (CPI / IIP latest released print with staleness, INR / WTI / US10Y 3M changes
  read one session late, India VIX same day), `regime.py` (breadth, divergence, vol / trend vector, labels).
- `ideation/features/{technical,flows,events}.py`, `tech_build.py` (assembly + `trainable_from` mask), `tech_cli.py`
  (`ideation features-tech build | regime | history`, registered by one `add_typer` line).
- `config/features_technical.yaml` (39 price / volume / delivery / ownership / FPI columns + 17 `regime_*` columns declared
  `kind: broadcast`), `config/features_events.yaml` (4 columns), `config/fpi_sector_map.yaml` (NSDL sector -> industry_group, both
  taxonomies, split at 2022-04-30). Registry gained sets `tech` (T0), `t1`, `regime` and a `trainable_from` key; no P3 column is
  in `core`/`ext`/`shown`. T0 = `tech`, T0_regime = `tech`+`regime`, T1 = `tech`+`t1`; 1Y `core_mom` / `core_ext` pick names from `tech`/`regime`.
- `pit/panel.py`: two rules added (see PIT rules) and `log_f` (factor effective on each session).
- Tests: `tests/unit/test_features_tech.py` (22), `tests/leakage/test_p3_leakage.py` (9), `tests/lake/test_p3_features.py` (11);
  `test_static_ban.py` now also scans `ideation/regime`. Two P2 tests were scoped to their own YAML (`names(source=...)`), which is
  the only edit to P2 tests.

### PIT rules and how the tests enforce them
- **Rows must be available on their own date.** A price row with `available_from > date`, or a factor with `available_from > eff_date`,
  is dropped by the panel (the snapshot has none: lake test counts 0 of 7,285,626 / all applied factors). Without this rule `poison_after`
  injects fake rows dated before the poison date and shifted factors that corrupt an event window at a later anchor.
- **Returns do not use the cumulative adjustment.** `r_t = close_t / close_{t-1} / exp(log_f_t)`; a corrupt factor dated after t cannot
  reach it. `close_adj` (levels, momentum, MAs) still uses all factors known at the anchor.
- **ear_3d**: window = sessions e'-1, e', e'+1 (e' = first session on/after the event date); the event is eligible only if s(e'+1) <= s(A) on
  the exchange calendar; the value is built from raw closes and the factors effective inside the window only, plus the group composite
  on those sessions. Poison test: everything with `available_from` after s(e'+1) is poisoned (nan / shuffle / inflate) at a later anchor,
  `ear_3d` unchanged to 1e-12, `pead_drift` changes (positive control); lake test: `ear_3d` of the same events at an anchor 15 sessions later differs by < 1e-9.
- **Foreign macro one-session lag** (INR=X, CL=F, ^TNX): a test perturbs only rows dated on the anchor (features unchanged) and then the day before
  (features change), since T-01 cannot detect a missing lag (the snapshot gives every daily code `available_from = date`). India VIX is same day (tested).
- **CPI / IIP**: month M visible from month end + 15 d (CPI) / + 45 d (IIP): the test checks the day before and the release day.
- **Ownership**: only frequency Y; latest row with a partner exactly 12 months earlier, <= 15 months old; quarterly rows with absurd values are in the
  synthetic snapshot and never leak in. Trainable from 2018-06.
- **T-01** (technical, flow, event, regime, RRG): synthetic (3 modes x 2 anchors) and lake (3 modes x {2019-06-28, 2023-06-30}, universe REBUILT
  on the poisoned store): frames bit-identical (`check_exact`); positive control with an earlier poison date changes the frame.
- `trainable_from` mask applied to the built matrix (test on synthetic and real 2019-02-28 matrices).

### Commands and runtimes
| command | result | runtime |
|---|---|---|
| `uv run ideation text features` (june,march,monthly) | 199 anchors, 273,230 rows | **44 s wall** (28.6 s build) |
| `uv run ideation text vendor-gate` | PASS (below) | 65 s wall |
| `uv run ruff check .` | clean | |
| `uv run pytest -m "not lake"` | 520 passed, 1 skipped, 1 xfailed, 1 xpassed | 125 s |
| `uv run pytest -m lake` | 91 passed (5 new PT2) | 329 s |
| `uv run python scripts/secret_scan.py --all` | clean | |
Total PT2 compute well inside the 10 min budget. New tests: unit `test_text_features.py` (8, incl. T-24 `test_text_availability` at
feature level and a synthetic poison test), lake `test_pt2_text.py` (T-25 `test_text_future_poison` at 2019-06-28, 2022-01-31,
2024-06-28 in all three modes bit-identical + positive control; vendor features poison; gate), leakage `test_static_ban.py` (+2).

### Text coverage, universe-restricted (share of members with cur / cur+prev transcript / PPT)
| anchor | U_score | cur | cur+prev | ppt | U_score_T | cur | cur+prev | ppt |
|---|---|---|---|---|---|---|---|---|
| 2016-06-30 | 827 | 13.1% | 7.5% | 22.7% | 971 | 10.4% | 6.1% | 18.9% |
| 2017-06-30 | 921 | 17.0% | 14.3% | 27.4% | 1115 | 13.8% | 11.6% | 23.0% |
| 2018-06-29 | 942 | 17.1% | 15.5% | 28.1% | 1058 | 15.0% | 13.6% | 24.2% |
| 2019-06-28 | 863 | 26.9% | 23.9% | 45.5% | 960 | 24.1% | 21.4% | 41.4% |
| 2020-06-30 | 857 | 26.4% | 24.7% | 44.0% | 946 | 23.6% | 22.3% | 40.4% |
| 2021-06-30 | 1172 | 25.5% | 23.0% | 41.7% | 1288 | 23.5% | 21.1% | 38.6% |
| 2022-06-30 | 1303 | 40.9% | 31.7% | 51.3% | 1355 | 37.2% | 29.1% | 46.5% |
| 2023-06-30 | 1409 | 51.2% | 47.6% | 58.1% | 1511 | 47.8% | 44.7% | 53.7% |
| 2024-06-28 | 1588 | 52.5% | 51.0% | 59.4% | 1725 | 47.2% | 46.1% | 54.0% |
| 2025-06-30 | 1672 | 55.9% | 54.1% | 62.9% | 1821 | 50.1% | 48.6% | 56.8% |
Monthly anchors, U_score_T, mean by year (cur / cur+prev): 2019 23.1/20.6, 2020 24.9/23.0, 2021 26.7/23.7, 2022 35.9/31.2,
2023 47.7/45.1, 2024 47.3/45.9, 2025 50.9/49.2%. Tactical text tier: train from 2019-01 has ~21% covered names, counted OOS from
2022-01 ~31-50%. 1Y: June 2019-2021 only ~24-27% covered, so 1Y text trials stay indicative. Pre-2015 June anchors have U_score of 13-25
names (the feature_complete hook) and zero text; the full table is in the CLI output / `features_text_coverage.parquet`.

### Vendor surprise validity gate (05 §6.3)
Estimator printed with the number: pooled Spearman(recomputed surprise, `ear_3d`), t from quarter-clustered (CR1) SE. Events
2015-01-01 .. 2025-06-30 with estimate and reported EPS: **n = 19,051, 42 quarters, rho = 0.318, t = 37.5 -> PASS (rho > 0, t > 3)**.
Fama-MacBeth over quarters: mean rho 0.321, t 48.8, 100% of quarters positive. Per-year rho 0.297-0.335 every year; beat rate
0.41-0.56, median |surprise| 13-28% (as in the 05 §0.1 probe). So trials #19-20 are NOT marked failed; MS2 may run them.
`ear_3d` here: sum of adjusted log-returns over sessions [s(e)-1, s(e)+1] minus the equal-weight mean of the same window over
other securities of the industry group (>= 3 peers; today's industry label).

### Deviations from 05 and why
- `outcomes/` not built (owner/orchestrator instruction; 05 §10.2.c).
- Gate period capped at 2025-06-30 (not "2015-2025"): the sealed tactical window starts 2025-07-31 and announcement returns after
  it are part of sealed 3M labels (05 §8). Events with s(e)+1 after the cap are not read.
- `ear_3d` is computed locally in `events_vendor.py` because P3's `features/events.py` is not merged. Re-run the gate against
  P3's `ear_3d` after the P3 merge (expected to agree; same definition).
- Rank-gauss (05 §7.3): raw features are the modelling input (a trial's R1 runs on the `text_covered` subset, so the pool is
  U_score at June anchors and U_score_T at monthly ones); `<f>_rg` columns are provided within covered members. R1 step c
  (zero-fill of NaN) would be a fill into uncovered names, which 05 forbids, so text trials must filter to covered rows
  (`isna_text == 0`) before R1; `isna_text_prev` rows additionally lack all `d_*` columns.
- The gate on the vendor read is two-layered: the view's caller check (P1) plus the `allow_vendor_estimates` flag argument; the
  builder cannot see `trial.config_json`, MS2 must pass it from the trial config.
- Trials #19-20 status in `ideation.experiment_trial` not touched (gate passed; nothing to mark failed).

### Risks
- Text coverage is survivors-only (today's companies) and thin before 2022; June 1Y coverage 24-27% for 2019-2021.
- yfinance estimates cannot be shown to be point-in-time; the gate's rho of 0.32 is consistent with as-of consensus but a
  vendor back-fill on part of the rows would also correlate with the announcement return. `isna` and coverage selection (analyst
  names only) remain; the challenger must still beat T1 on the same names.
- Month-granular doc_date plus PT1's month-end floor make availability conservative (a late-month call is usable ~1-2 months
  late); some signal is lost, none leaked. 0.7% of parsed `filed_date`s precede q_end (PT1 note): the month floor still applies.
- Downstream builders (MS1/MS2) must select columns by `reg.names(source=...)`/`sets=`; `reg.names()` now spans text and vendor.
- Doc-level quality filters (e.g. minimum `n_tok`) are not applied; short or garbled docs add noise to the deltas.

| `uv run ideation features-tech build --anchors monthly --workers 6 --force` | 199 monthly anchors (2010-04 .. 2026-10), 337,787 rows (every `traded_ok` security, flags `in_u_score` / `in_u_score_t`), 68 columns | **150 s** (limit 480 s; 1 worker is ~3.2 s per anchor in-process, ~11 min) |
| `uv run ideation features-tech history --from 2020-01 --to 2020-07` | stored regime history | <2 s |
| `uv run ideation features-tech regime --as-of latest` | regime vector, labels, RRG (22 groups, 12 sectors, 14 indices) at 2026-11-05; upserted `regime_snapshot` + 48 `sector_rotation` rows | ~8 s |

| `uv run ruff check .` | All checks passed | <1 s |
| `uv run pytest -m "not lake"` | **541 passed**, 1 skipped, 1 xfailed, 1 xpassed (E1 documented) | 105 s |
| `uv run pytest -m lake` | **97 passed** (86 earlier + 11 P3) | 322 s |
| `uv run python scripts/secret_scan.py --all` | clean | <1 s |

Outputs (not in git): `outputs/<snap>/features_tech.parquet`, `regime_history.parquet`.

### Measured
- COVID low, 2020-03-31: `pct_above_200dma` **6.95%**, `pct_above_50dma` 2.2%, VIX 64.4 (percentile 98.9), Nifty 500 200-day gap -25%,
  drawdown -31%, labels Down / Stressed / Narrow (acceptance < 15%).
- Latest (2026-11-05): 50.0% above 200dma, VIX 15.8, labels Transition / Stressed / Neutral. CPI and IIP are NaN (last prints 2025-12 / 2026-03 are stale).
  RRG leaders: Services, Construction Materials, Media, Textiles, IT, Autos, Realty; laggards: Chemicals, Oil & Gas, Capital Goods, Healthcare.
  The three indices that start in 2026-10 are excluded.

### Deviations (and why)
- **Workers use `spawn`, not `fork`**: forked workers died silently (numpy Accelerate on macOS), so the pool hung. Spawned workers rebuild the store (~3 s).
- **Membership** comes from the stored universe table (`PitView.universe()`; in-memory `build_universe` is ~1-2 s extra per anchor and would blow the budget). T-01 on the lake
  rebuilds it on the poisoned store (`--rebuild-universe` in the CLI). Anchors not in the table (e.g. the latest session) are built in memory.
- **Composites** are the EW mean of every company-linked security with a valid return and a liquid trailing ADV (>= 0.2 cr over the previous 63 sessions, t excluded), not the anchor's
  universe members, so a composite value on a date is the same at every anchor. 01 §3.1 wants "full PIT universe" for breadth: breadth uses the anchor's `in_u_score_t` members.
- **RRG weekly points are anchor-aligned** (A, A-5, A-10, ...), not Friday closes; the z windows use `min_periods` 26 of 52 so RRG exists from about 2011-09.
- `config/lags.yaml` and `config/indices.yaml` of 01 P3 were not created: lags are code constants (P0 decision) and the index list is a constant in `rotation.py`.
- `brk_*` volume confirmation uses the raw ratio ADV20 / ADV126 > 1.5 (05 §6.2's `vol_ratio > 1.5` is a log column otherwise); `brk_3_4y` is the 1008-session high with >= 756 valid sessions.
- `beta_252` falls back to 1.0 (clip [-1, 3]) with no flag; `rrg_quad_grp` is ordinal (Lagging 0, Improving 1, Weakening 2, Leading 3).
- Fundamental `features build` and universes untouched; features parquet covers every `traded_ok` security, not only U_score_T.
- The "base-2024" CPI code of 01 §3.3 is not in the snapshot; the 2012-base series is used.

### Risks
- Live-state footprint: worktree runs wrote `run` rows (kinds `features_tech`, `regime`), `run_manifest` rows, one `regime_snapshot` row and 48 `sector_rotation` rows for 2026-11-05 into the live `ideation` schema.
- `earnings_dates` come from Yahoo and cover 400-750 companies a year (mostly large / mid caps): `isna_event` is 1 for ~78% of rows; a wrong event date shifts the window silently.
- Sector / industry labels and the stock-to-company link are today's (disclosed hindsight, as in P2).
- CPI / IIP are NaN at late anchors (series stop); the broadcast scaler sets missing to the training mean, so a regime model trained on 2014-2025 sees "average inflation" in 2026.
- 12 of 17 regime columns are only weakly informative on a 36-month window (36 distinct values per column); the 90/10 rule decides whether regime stays.
- Ownership changes are updated once a year (April) and are stale between; they are T1 only.

## MS2: tactical bake-off (family `tactical-3M`, branch `phase-ms2-tactical-bakeoff`)

Date 2026-11-11. Snapshot `20261105-e33e7986`. `ideation trials register --family tactical-3M` inserted 25 rows (N = 25;
the family was empty before). `config/trials_tactical.yaml` is not edited after registration (any change is a new hash and a
new trial). `ideation init-db` is never run from this worktree; the real ledger is already at version 12.

### Protocol frozen BEFORE any fit (forking-path guard)
1. **Horizons.** 3M is the only selecting horizon. 1M comes from the SAME fits (3M target, embargo 3 anchors) scored against
   the 1M labels of the same test anchors; it therefore inherits the stricter 3M embargo. Never used for selection.
2. **Target.** Rank-gauss of `ret_total` (3M) within the trial's universe at each anchor (U_score_T; U_score_T with
   ADV63 >= 0.5 cr for #21). R1 = `rank.transform.xs_transform` (global spec) per anchor; one matrix per (feature set,
   row subset), cached; legal because R1 is per cross-section.
3. **Feature sets by source/set, never `reg.names()` globally.** T0 = `tech`; T0_regime = `tech` + `regime` (broadcast columns
   passed to `make_learner(broadcast=...)`); T1 = `tech` + `t1` (`isna_event` passes through raw, it is already a flag);
   T1_text = T1 + `text`; T1_vsurp = T1 + `vendor`. `scr` gets the priors of the trial's own columns only.
4. **Windows.** Rolling 36 usable anchors, 12-anchor test blocks, purge `label_end <= T` (03 C3), embargo 3 anchors.
   T0 counted OOS 2014-01..2025-06 (12 blocks); the first block trains on fewer than 36 anchors (history starts 2011-01).
   T1 / VSURP: train_start 2020-04-30, counted OOS 2023-01..2025-06; TEXT: train_start 2019-01-31, counted OOS 2022-01..2025-06.
   Blocks start at each trial's own counted-OOS start; short first-window training sets are reported.
5. **Subset trials (#17-20).** Filter to `isna_text == 0` (resp. `isna_vsurp == 0`) BEFORE R1 so nothing is zero-filled into
   an uncovered name. `allow_vendor_estimates=True` is read from the trial config only. Paired control = the same learner on
   T1 columns only, same covered rows, same folds, same `train_start` (a protocol arm, NOT a new trial, not in N). Paired
   per-anchor delta-IC with a stationary bootstrap (>= 10,000). Secondary: the full-universe T1 trial scores restricted to the
   same names. #19-20 run only if the vendor gate (re-run with P3's `ear_3d`) passes.
6. **Selection pool for 90/10.** Window-comparable pool = the 21 fitted/composite trials with counted OOS 2014-01..2025-06
   (#1-13, #21, #25, plus consensus #22-24 for IC, no stability => z = 0). The short-window trials (#14-20) are compared
   only by paired deltas over their own window (vs the T0 trial restricted to the same anchors / same names) and by their own
   IC; they are not z-scored against the long-window pool. Selecting rule: `0.9 z(mean OOS IC, 3M) + 0.1 z(stability)`.
7. **Consensus scalar scores** (members = the 10 `consensus_members`, p = per-anchor percentile): CONS_MAJ_T = the 6th largest
   p across the members (>= 0.9 iff >= 6 members have the name in D10); CONS_UNAN_T = min over members of p (>= 0.9 iff all 10
   have it in D10); ENS_RANKAVG_T = mean p. These scores feed IC, DSR and PBO; the set-level C_maj / C_unan results are reported.
8. **CPCV rule.** Top 3 by the 90/10 score among the *fitted single-model* trials plus the baselines; a consensus trial in the
   top 3 is replaced by running CPCV for its members only if budget allows, otherwise reported as "not run" with the reason.
   Baselines (no fit): T_EW_SIGNPRIOR and MOM_12_1 (and the other 3.6 benchmarks). CPCV: 12 groups over 2011-01..2025-06 (66
   splits, 11 paths), embargo 3.
9. **PBO** = CSCV, S = 12, on the T x N matrix of monthly OOS ICs of the long-window pool; trials with shorter series are
   excluded from the matrix (reported). **DSR** on the IC series and the cost-adjusted Q5-Q1 series with N = 25 and the
   cross-trial variance of per-observation Sharpe ratios of the pool.
10. **Baseline that ships if the champion fails.** Non-inferiority (C9, 02 5.5 gate 3) is tested against T_EW_SIGNPRIOR (the
    sign-prior composite of the same tactical features) AND against MOM_12_1; if the champion fails the gates, the better of
    the two baselines by mean 3M IC ships and the report says so. Tactical gates (02 5.5): mean IC >= 0.02 with NW t >= 2.0
    (lag 2 for 3M, 0 for 1M), 95% block-bootstrap CI excludes 0, DSR >= 0.95, cost-adjusted Q5-Q1 > 0 after 2 x 12 bps x
    measured turnover, positive in >= 55% of months, PBO <= 0.5, adversarial AUC <= 0.70.
11. **Storage.** `ideation.model` row per (trial, horizon) with status `candidate` (never `champion`: promotion is manual, A12)
    feeds `wf_result`; `oos_prediction` holds the 3M rows (no horizon column); 1M scores live in parquet + `wf_result` rows of
    the 1M model; `experiment_trial` gets status, n_oos, ic_series, spread_series, run_id.
12. **Compute.** <= 4 threads, `nice -n 10`, per-(trial, fold) checkpoints keyed by config hash + code version. Bootstrap >= 10,000.
    Fixed hyperparameters (no tuning); lgbm/xgb weak-signal attenuation (E1 T-05) is a known limitation and is reported.

### Hygiene disclosure
During orientation a scratch script read `labels.parquet` directly (outside `load_labels`) and printed aggregates over all
horizons (anchor ranges, non-null shares, exit-type counts, the first rows of the 2010 anchors). No sealed return value was
printed or used. Every later label read goes through `load_labels(unsealed_only(...))`.

### What was built (MS2)
- `ideation/tactical/` (own Typer sub-app `ideation tactical …`, one `add_typer` line in `ideation/cli.py`): `data.py` (panel, R1
  per feature set / row subset, rank-gauss target, labels only via `load_labels(unsealed_only(...))`, anchors <= 2025-06-30),
  `engine.py` (WFO and CPCV with per-(trial, fold|split) parquet checkpoints keyed by config hash + `CODE_VERSION`),
  `evalx.py` (per-anchor metrics, IC inference, paired deltas, DSR, CSCV PBO, 90/10, gates, consensus scores), `report.py`,
  `persist.py` (model / wf_result / oos_prediction / experiment_trial), `vendor.py` (vendor features, only when the trial config
  sets `allow_vendor_estimates` and the gate passed), `cli.py`. `tests/unit/test_tactical.py` (4 tests: sealed-anchor
  discipline through `load_labels`, checkpoint resume + C3 purge + embargo, 66 CPCV splits, consensus scalar definitions);
  `tactical` added to the static-ban scan. `events_vendor.validity_gate_p3` + `ideation text vendor-gate --ear p3`.
- No migration. `ideation init-db` never run.

### Commands and runtimes (Mac shared with MS1; `nice -n 10`, <= 4 threads)
| command | result | wall time |
|---|---|---|
| `ideation trials register --family tactical-3M` | 25 rows (N = 25) | 2 s |
| `ideation text vendor-gate --ear p3` | PASS | ~3 min |
| `ideation tactical wfo` (21 fitted trials + 4 paired control arms, resumable) | all done | **about 85 min of compute** (RF_T0 554 s, MLP 373 s, SVR 645 s, RF_T0_REGIME 503 s, RF_T0_ADV05 399 s, RF_T0_9010SEC 426 s, RF_T1 219 s; linear/boosters 12-105 s) |
| `ideation tactical cpcv` (SCR_T0, OLS_T0, RIDGE_T0 + T_EW_SIGNPRIOR; 66 splits each) | done | 87 s |
| `ideation tactical report --persist` | done (adversarial AUC ~8 min, persistence ~10 min) | ~20 min |

### Vendor validity gate re-run with P3's `ear_3d` (instead of PT2's local copy)
Events 2015-01-01..2025-06-30, vendor events matched to P3 `ear_3d` at monthly anchors (same event: `ear_days` agrees with the
vendor event date; 99.86% of 19,093 events matched): **n = 19,066, 42 quarters, rho = 0.3180, t (quarter-clustered CR1) = 37.20 ->
PASS** (PT2 local copy: rho 0.318, t 37.5). Fama-MacBeth mean rho 0.321, t 49.8, 100% of quarters positive. Trials #19-20 ran.

### Results, horizon 3M (primary), counted OOS 2014-01..2025-06, 138 monthly anchors, U_score_T
IC = mean per-anchor rank IC vs 3M total return; t = Newey-West lag 2; CI95 = stationary block bootstrap (10,000); cost-adj
Q5-Q1 = 3M spread minus 2 x 12 bps x (turnover Q5 + turnover Q1) measured month on month (02 5.5 literal; a conservative
full-round-trip variant is in the report); DSR/PBO below.
| trial | IC | NW t | CI95 | % months > 0 | cost-adj Q5-Q1 | 1M IC (lag 0 t) |
|---|---|---|---|---|---|---|
| ENS_RANKAVG_T | **0.1258** | 6.39 | [0.083, 0.167] | 84.1% | 0.0519 | 0.1014 (7.99) |
| CONS_MAJ_T | 0.1240 | 6.33 | [0.082, 0.165] | 83.3% | 0.0504 | 0.0998 |
| CONS_UNAN_T | 0.1225 | 6.99 | [0.085, 0.159] | 85.5% | 0.0490 | 0.0996 |
| SCR_T0 | 0.1209 | 5.22 | [0.072, 0.170] | 79.7% | 0.0386 | 0.1045 |
| RIDGE_T0 | 0.1195 | 5.93 | [0.076, 0.162] | 83.3% | 0.0458 | 0.0945 |
| RIDGE_T0_REGIME | 0.1197 | 5.95 | [0.076, 0.162] | 83.3% | 0.0457 | 0.0945 |
| OLS_T0 | 0.1187 | 5.94 | [0.075, 0.161] | 83.3% | 0.0455 | 0.0940 |
| RF_T0_ADV05 (own universe) | 0.1122 | 5.88 | [0.071, 0.152] | 83.3% | 0.0445 | 0.0887 |
| PRIDGE_T0 | 0.1097 | 6.53 | [0.074, 0.144] | 86.2% | 0.0466 | 0.0886 |
| RF_T0_9010SEC | 0.1051 | 5.72 | [0.066, 0.143] | 83.3% | 0.0409 | 0.0858 |
| RF_T0 | 0.1037 | 5.68 | [0.066, 0.141] | 84.1% | 0.0431 | 0.0841 |
| **T_EW_SIGNPRIOR (baseline)** | **0.1002** | 5.38 | [0.059, 0.143] | 79.7% | 0.0443 | 0.0758 |
| XGB_T0 | 0.0993 | 5.78 | [0.063, 0.134] | 83.3% | 0.0392 | 0.0785 |
| MLP_T0 | 0.0973 | 6.24 | [0.065, 0.128] | 84.1% | 0.0444 | 0.0787 |
| LGBM_T0 | 0.0948 | 5.72 | [0.061, 0.128] | 84.1% | 0.0382 | 0.0770 |
| RF_T0_REGIME | 0.0942 | 4.98 | [0.056, 0.134] | 79.7% | 0.0386 | 0.0823 |
| SVR_T0 | 0.0844 | 5.94 | [0.054, 0.114] | 85.5% | 0.0375 | 0.0714 |
| LGBM_T0_REGIME | 0.0825 | 4.76 | [0.046, 0.120] | 77.5% | 0.0340 | 0.0762 |
| MOM_12_1 (benchmark) | 0.0716 | 4.31 | [0.036, 0.106] | 76.1% | 0.0463 | 0.0444 |
| LOWVOL (benchmark) | 0.0920 | 4.41 | [0.049, 0.137] | 68.8% | 0.0125 | 0.0946 |

Short-window trials (own windows, not in the selection pool): RF_T1 0.0933, LGBM_T1 0.0972, RIDGE_T1 0.0983 (2023-01..2025-06,
30 anchors); RF_T1_TEXT 0.0582 and RIDGE_T1_TEXT 0.0741 (covered names, 2022-01.., 42 anchors); RF_T1_VSURP 0.0398 and
RIDGE_T1_VSURP 0.0537 (vendor-covered names, 2023-01.., 30 anchors).

### Selection, DSR, PBO, gates (03 C9, 02 5.5)
- **90/10** (0.9 z(IC) + 0.1 z(stability)) over the 18 long-window trials: ENS_RANKAVG_T 1.20, CONS_MAJ_T 1.08, CONS_UNAN_T 0.99, SCR_T0
  0.88, OLS_T0 0.84, RIDGE_T0 0.80, RIDGE_T0_REGIME 0.80, RF_T0_ADV05 0.45 ... SVR_T0 -1.51, LGBM_T0_REGIME -1.67. Stability is NaN
  (z = 0) for T_EW, SVR (cost, below) and the consensus trials. Top 3 fitted single models: SCR_T0, OLS_T0, RIDGE_T0.
- **DSR** (N = 25, per-observation Sharpe of the 3M IC series against the pool's SR variance): 1.000 for every pool trial on the IC
  series (0.980-0.999 on the cost-adjusted spread series). The pool's IC-IR is 0.5-0.85 per month with 138 observations, so the
  gate is not binding here (unlike 1Y).
- **PBO** (CSCV, S = 12, T = 138 x 18 trials): **0.232**.
- **Champion by the 90/10 rule: ENS_RANKAVG_T.** Gates: IC >= 0.02 PASS (0.1258); NW t >= 2 PASS (6.39); CI95 excludes 0 PASS;
  DSR >= 0.95 PASS; cost-adj Q5-Q1 > 0 PASS (+5.2%); months > 0 >= 55% PASS (84%); PBO <= 0.5 PASS; **adversarial AUC <= 0.70
  FAIL (T0 mean 0.945, T1 0.958)**; non-inferiority vs T_EW_SIGNPRIOR PASS (delta IC +0.0257, CI90 [+0.010, +0.041], P(>0) = 1.00);
  vs MOM_12_1 PASS (delta +0.054, CI90 [+0.027, +0.080]).
- **Decision under the frozen rule: the baseline ships (T_EW_SIGNPRIOR, the better baseline by IC: 0.1002 vs MOM_12_1 0.0716), because the
  champion fails the blocking adversarial-validation gate.** Plain reading for the owner: the gate that fails is an input-level
  property that applies equally to T_EW_SIGNPRIOR (same R1 matrix), and on every performance criterion the champion beats the baseline.
  The owner may decide whether the AUC finding (below) is disqualifying; per 03 C9 a model is not promoted automatically
  (`candidate` only, A12).

### Adversarial validation (02 6.1) and why it fails
Train-vs-test classifier AUC on the R1 inputs (broadcast columns excluded), per WFO fold: T0 0.91-1.00, mean 0.945 (T1 0.93-0.98).
Top separating features are the rank-gauss of the binary breakout columns (`brk_*`, `brkdn_*`) and their `isna_` flags: a rank-gauss of a
0/1 column is an anchor-dependent constant pair, i.e. a hidden regime proxy, plus `trainable_from` masking in 2014-15. With binary
breakout and flag columns dropped the T0 mean AUC is still 0.763 (group RRG columns, 52-week distance), T1 0.884 (`prom_chg_1y`,
`fpi_sector_flow_3m`). The per-cross-section rank transform therefore does not make these inputs stationary across 3-year windows.
The 02 6.1 remedy (drop or re-transform the top shift feature and rerun as a NEW trial) was NOT done: it would add trials to
N after seeing results. It is the first recommended follow-up.

### Tier, regime, 90/10-a, universe and paired arms (per-anchor delta IC, stationary bootstrap 10,000)
| arm (a - b) | n | IC a | IC b | delta | CI90 | P(delta > 0) |
|---|---|---|---|---|---|---|
| RF_T0_REGIME - RF_T0 | 138 | 0.0942 | 0.1037 | -0.0095 | [-0.025, +0.004] | 0.13 |
| LGBM_T0_REGIME - LGBM_T0 | 138 | 0.0825 | 0.0948 | -0.0123 | [-0.030, +0.003] | 0.10 |
| RIDGE_T0_REGIME - RIDGE_T0 | 138 | 0.1197 | 0.1195 | +0.0001 | [-0.0004, +0.0006] | 0.66 |
| RF_T0_9010SEC - RF_T0 | 138 | 0.1051 | 0.1037 | +0.0013 | [-0.0004, +0.0031] | 0.90 |
| RF_T0_ADV05 - RF_T0 (different universe) | 138 | 0.1122 | 0.1037 | +0.0085 | [+0.003, +0.014] | 1.00 |
| RF_T1 - RF_T0 (2023+ anchors) | 30 | 0.0933 | 0.0851 | +0.0082 | [-0.005, +0.020] | 0.85 |
| LGBM_T1 - LGBM_T0 | 30 | 0.0972 | 0.0804 | +0.0168 | [+0.003, +0.029] | 0.97 |
| RIDGE_T1 - RIDGE_T0 | 30 | 0.0983 | 0.0900 | +0.0083 | [0.000, +0.017] | 0.95 |
| RF_T1_TEXT - same learner on T1 columns, same covered rows/folds | 42 | 0.0582 | 0.0626 | -0.0045 | [-0.014, +0.004] | 0.21 |
| RIDGE_T1_TEXT - control | 42 | 0.0741 | 0.0797 | -0.0057 | [-0.013, +0.001] | 0.10 |
| RF_T1_VSURP - control | 30 | 0.0398 | 0.0362 | +0.0036 | [-0.009, +0.015] | 0.70 |
| RIDGE_T1_VSURP - control | 30 | 0.0537 | 0.0555 | -0.0017 | [-0.009, +0.005] | 0.34 |
- **Regime** does not help: it hurts the trees (-0.010 / -0.012, P(>0) 0.10-0.13) and cannot matter for a linear model (a
  broadcast column shifts all scores at an anchor equally; the ridge IC is identical to 4 decimals by construction).
- **T1 tier** (delivery, FPI, ownership, event features) adds +0.008 to +0.017 IC over T0 on the same 2023+ anchors; only LGBM's CI90
  excludes 0. Suggestive, not conclusive (30 anchors).
- **Text (#17-18)** does not add on covered names (both slightly negative vs the matched control). **Vendor surprise (#19-20)** adds
  nothing (RF +0.004, ridge -0.002, CIs straddle 0). Both are reported as negative results. These covered subsets are tiny
  (~7.7k and ~10.8k training rows in the first fold) and their IC levels (0.04-0.08) are well below the full-universe T1 models:
  the covered names are a different, harder cross-section.
- **90/10 reading (a)** (RF_T0_9010SEC) is indistinguishable from RF_T0 (+0.0013).
- **Models**: linear models (scr, ridge, ols, 0.119-0.121) beat the forest (0.104), MLP, XGB, LGBM (0.095, attenuation of weak
  signals, the E1 T-05 finding) and SVR (0.084); PRIDGE 0.110. The sign-prior composite alone (0.100) is only 0.02 below the best
  single model and statistically close to it. **Averaging the 10 members' ranks (ENS_RANKAVG_T) is the best result** (0.1258), +0.0257
  over the composite.
- Consensus sets (3M): C_maj (>= 6 of 10 members in D10, mean size 57 names) beats the universe median by +5.3%; C_unan (all 10
  agree, mean 2.4 names, valid in few months) by +1.7%; the size-matched top-|C| of the rank average +5.9%. Agreement narrows the
  set but does not improve the mean over the rank average.

### CPCV (12 groups, k = 2, 66 splits, 11 paths, embargo 3) over all 174 anchors 2011-01..2025-06
| trial | mean path IC | min..max path | NW t (mean) | cost-adj Q5-Q1 | % months > 0 |
|---|---|---|---|---|---|
| RIDGE_T0 | 0.1523 | 0.1505..0.1546 | 8.43 | 0.0608 | 84.0% |
| OLS_T0 | 0.1497 | 0.1479..0.1518 | 8.52 | 0.0601 | 83.8% |
| SCR_T0 | 0.1462 | 0.1447..0.1473 | 6.57 | 0.0491 | 79.2% |
| T_EW_SIGNPRIOR (no fit) | 0.1194 | n/a | 6.53 | 0.0523 | 80.5% |
| MOM_12_1 (no fit) | 0.0913 | n/a | 5.35 | 0.0500 | 77.5% |
The linear models are stable across paths (spread 0.002-0.004) and beat the baseline by ~0.03 on all 174 anchors. CPCV IC is higher
than WFO because it trains on both sides of each test group and includes 2011-13. **Not run:** CPCV for the champion
ENS_RANKAVG_T and its heavy members. Measured per-split cost on ~160k training rows: RF 238 s (~4 h for 66 splits), MLP 108 s
(~2 h), SVR 67 s (~1.2 h), LGBM 5 s; the consensus needs all 10 members, far above the 2.5 h CPCV budget. Hyperparameters were
not touched to make it fit.

### Honest caveats on the magnitude
- IC 0.10-0.13 at 3M is far above what such price features usually give. It is concentrated: mean IC by year for the ensemble
  2014 0.14, 2015 0.12, 2016 0.12, 2017 0.20, **2018 0.21, 2019 0.23**, 2020 -0.03, 2021 0.02, 2022 0.18, 2023 0.09, 2024 0.14, 2025 0.05 (H1);
  T_EW -0.04 in 2020 and -0.01 in 2025; LOWVOL alone 0.25 in 2018-19. It is mostly a low-volatility / small-illiquid-name effect that
  works in drawdown regimes (2018-19 small-cap crash, 2022) and fails in 2020-21. By liquidity (T_EW, RF, MOM, LOWVOL IC): ADV < 0.5 cr
  0.06/0.07/0.05/0.04; 0.5-2 cr 0.13/0.16/0.09/0.12; 2-10 cr 0.11/0.12/0.07/0.09; > 10 cr 0.08/0.08/0.05/0.07, so it survives in liquid names
  but at roughly 70-80% of the headline value.
- Monthly anchors with 3M labels overlap; the t statistics use NW lag 2 and a block bootstrap, but the effective sample is smaller than 138.
- Survivorship: universe includes delisted names with the P1 exit policy, but sector/industry labels and the stock-company link are today's.
- Cost-adjusted spreads charge the measured month-on-month turnover; a real 3-tranche book would also pay full round trips (the report's
  `spread_cost_full`, 48 bps per 3M tranche, lowers the champion's Q5-Q1 from 0.0536 to 0.0488 and stays positive).
- The 1M horizon (same fits, 3M target, embargo 3, never selecting): IC 0.071-0.105, ensemble 0.1014, MOM_12_1 only 0.044.

### Deviations and risks (plain)
1. **Champion fails a blocking gate; the baseline ships by the frozen rule.** The AUC failure is structural (above), shared by the baseline.
2. **90/10 stability computed with a reduced importance protocol** (3 repeats, 2,000 seeded test rows per fold, instead of 20 repeats on all rows;
   the same for every trial); **SVR stability not computed** (explicit-loop RBF predict, ~2 min per 14k rows) and the consensus/T_EW trials have none, so
   z = 0 for them. Stability is 10% of the score; with it ignored the order of the top group does not change (the ensemble leads by IC).
3. **No CPCV for the champion/its heavy members** (cost, above). CPCV covers the top 3 fitted single models and the baselines.
4. Text and vendor trials run on covered subsets with paired control arms (protocol arms, not trials, not in N = 25); #19-20 YAML configs
   do not declare `subset`; the vendor-covered subset (`isna_vsurp == 0`) was applied by the driver because of the gate key `vsurp_validity`.
5. Short first training windows: the first T0 fold trains on 31 anchors (history starts 2011-01, embargo 3); T1/VSURP first block 28 anchors (train_start 2020-04-30), TEXT 31 (train_start 2019-01-31).
6. The CPCV and WFO IC windows differ (174 vs 138 anchors); do not compare their levels.
7. T-04/T-05 learner caveats from E1 stand: lgbm/xgb attenuate weak signals (here: LGBM 0.095, XGB 0.099 vs ridge 0.120); hyperparameters were not tuned.
8. `ideation.model` rows (status `candidate`, two per trial: 3M and 1M), `wf_result` (138 rows per model, 3M and 1M), `oos_prediction`
   (3M rows only; ~162k per long-window trial) and `experiment_trial` (status `ok`, `ic_series`, `spread_series`) were written to the live `ideation` schema.
   `wf_result.ic_lo/ic_hi` are NULL (not computed per anchor). `rank_calibration` is MS3's. Controls/benchmarks are in parquet/report only.
9. Data and checkpoints: `IDEATION_DATA_DIR/tactical_ms2/{wfo,cpcv,scores,MS2_REPORT.md,CPCV_REPORT.md,decision.json,adversarial_auc.json}` (not in git).

### Acceptance
See the command table of this entry; ruff, `pytest -m "not lake"`, `pytest -m lake`, secret scan results are in the final line below.
| check | result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest -p no:xdist -m "not lake"` | **555 passed**, 1 skipped, 1 xfailed, 1 xpassed (E1's documented xfail/xpass) in 140 s (includes 4 new MS2 tests and the static-ban scan of `ideation/tactical`) |
| `uv run pytest -p no:xdist -m lake` | **101 passed, 1 failed** in 413 s. The failure is `tests/lake/test_e1_ddl.py::test_register_then_train_guard`: it registers `1Y-june` inside a rolled-back transaction and asserts `n_family == 50`, but the live `experiment_trial` already holds MS1's 50 `1Y-june` rows (and this test's hashes are new inserts), so it sees 100. It is an interaction with the concurrent MS1 registration in the shared real schema, not with MS2 code (MS2 touched nothing in `trials/`). It needs the test to count only its own rows (or run before MS1 registers). |
| `uv run python scripts/secret_scan.py --all` | clean |

## MS2 diagnostics (branch `phase-ms2-diagnostics`, diagnostics not trials; N unchanged, selection untouched)

Date 2026-11-11. Code `ideation/tactical/diagnostics.py`, CLI `ideation tactical diagnostics` (about 4 min, 4 threads, `nice -n 10`),
tests `tests/unit/test_tactical_diagnostics.py` (planted beta-only signal is killed by neutralisation, planted alpha survives).
Full output: `~/Ideation-data/tactical_ms2/DIAGNOSTICS.md` (+ `diagnostics_key.json`, not in git). Uses the stored OOS scores of the
counted OOS 2014-01..2025-06 (138 anchors); nothing refit, no DB write. The five headline means reproduce exactly
(ENS 0.1258, SCR 0.1209, RIDGE 0.1195, T_EW 0.1002, MOM 0.0716).

**Method.** Controls are PIT/trailing at each anchor: `beta_252` (daily OLS beta to the Nifty 500, 252 sessions, `features.technical`),
`rvol_63`, `idio_vol`, ln mcap, ln ADV63. Residual IC = rank IC of the score against the 1/99-winsorised 3M return residualised per
anchor on B (beta), BV (beta+rvol+idio_vol), BVLS (BV+size+ADV). Within-tercile IC = mean of the three within-tercile ICs. Entry lag:
`labels.build.compute_labels` in memory (nothing written) with `entry_session` = 2nd / 5th good session after the anchor and the
unchanged `label_end` (03 C3; the first trade on or after the entry session, as in the stored labels); the s1 recompute reproduces the
stored labels (162,645 rows, max abs diff 4e-13). Caveats: beta_252, rvol_63, idio_vol are T0 inputs of the models (neutralising removes
the part of the model that is by design a low-risk tilt, which is a risk premium rather than a bug); daily beta of illiquid names is
biased down, so beta partly proxies illiquidity; per-year NW t with 12 overlapping points is only indicative.

| mean 3M IC (NW t) | raw | res B | res BV | res BVLS | within-beta terc. | within-vol terc. |
|---|---|---|---|---|---|---|
| ENS_RANKAVG_T | 0.1258 (6.4) | 0.1169 (7.6) | 0.1074 (10.8) | 0.1139 (12.9) | 0.1170 (7.3) | 0.1026 (6.7) |
| SCR_T0 | 0.1209 (5.2) | 0.1116 | 0.1002 | 0.1046 | 0.1096 | 0.0961 |
| RIDGE_T0 | 0.1195 (5.9) | 0.1099 | 0.0955 | 0.1020 | 0.1093 | 0.0956 |
| T_EW_SIGNPRIOR | 0.1002 (5.4) | 0.0936 | 0.0935 | 0.0990 | 0.0915 | 0.0851 |
| MOM_12_1 | 0.0716 (4.3) | 0.0750 | 0.0757 | 0.0804 | 0.0764 | 0.0815 |

The NW t rises after residualising because the common market-driven swings of the IC series are removed; the mean falls only 7-18%.
ENS per year (raw -> BVLS): 2014 .142 -> .162, 2015 .122 -> .099, 2016 .115 -> .114, 2017 .199 -> .184, **2018 .205 -> .131, 2019 .233 -> .067**,
2020 -.030 -> .051, 2021 .018 -> .133, 2022 .184 -> .128, 2023 .088 -> .118, 2024 .145 -> .105, 2025 .053 -> .038. The 2018-19 peak is mostly a
beta/vol effect (2019 loses 71%); the 2020-21 "failure" is also beta/vol (residual IC is positive: .051 and .133). Exposure: mean corr(score, beta)
-0.29, corr(score, rvol_63) -0.46, corr(score, ln mcap) +0.23, corr(score, ln ADV) +0.03; mean beta(Q5) - beta(Q1) = -0.35. Per-anchor raw IC
correlates -0.67 with the universe equal-weight 3M return (raw IC 0.211 when it is negative (51 anchors) vs 0.076 when positive (87)); the
BVLS-residual IC correlates -0.21 and is regime-flat (0.118 vs 0.111).

Entry lag (mean IC, ENS): s1 0.1258, s2 (1 session later) 0.1272, s5 (4 sessions later) 0.1292; SCR 0.1209 / 0.1244 / 0.1294; RIDGE 0.1195 /
0.1215 / 0.1236; T_EW 0.1002 / 0.1035 / 0.1071; MOM 0.0716 / 0.0720 / 0.0724 (NW t of ENS 6.4 / 6.5 / 6.8). BVLS residual IC at s5: ENS 0.1129
(vs 0.1139 at s1). The IC against the s1-to-s5 return alone is small and positive (ENS 0.034), so there is no bid-ask-bounce signature.

Buckets (ENS): ADV tercile low / mid / high 0.158 / 0.136 / 0.101 (high-minus-low -0.057, t -3.9); mcap tercile 0.142 / 0.095 / 0.068; against the
BV-residual return the ADV terciles are 0.134 / 0.117 / 0.083. So the signal is strongest in small illiquid names but still 0.08 (BV residual) in the most
liquid third. Standalone T0 feature IC (raw): dist_52wh +0.103 (t 5.1), dvol_63 -0.100 (4.5), rvol_63 -0.092 (4.4), ma50_200 +0.071, mom_12_1 +0.072,
rs_n500_6m = mom_6m (identical columns) +0.063, max_21 -0.059, idio_vol -0.085, ma200_gap +0.067, beta_252 alone -0.047 (t -2.3); breakout flags, volume, RRG
and liquidity features are near zero. The 2018-19 IC of the low-vol features is -0.25 to -0.27 (i.e. strong low-vol payoff) and about 0 in 2020-21.

**Verdict.** The beta/vol-bet hypothesis explains the timing of the IC, not its level. About 85-93% of the 0.126 survives beta or beta+vol
neutralisation (0.117 / 0.107, 0.114 with size and liquidity) and about 82-93% survives the within-tercile tests; the 2018-19 spike (0.21-0.23) shrinks
to 0.07-0.13 and the poor 2020-21 turns positive, which makes the neutral alpha roughly 0.11 and regime-independent. A 1-4 session entry delay does
not reduce the IC at all (0.126 -> 0.127 -> 0.129; residual 0.114 -> 0.113), so reversal or bid-ask bounce in illiquid names is not what drives it, although the
effect is concentrated in small, less liquid names (IC 0.158 low ADV vs 0.101 high ADV), which limits capacity. The effect is not an artefact of the 0.126 headline,
but the headline overstates it: roughly 0.11 is the figure to carry forward and the part above that is a defensive low-vol / low-beta tilt that pays in drawdowns.
Portfolio implication: the score is correlated -0.3 with beta and -0.45 with vol, so a Black-Litterman view built on the raw score would double-count the low-beta
tilt already priced through the covariance/market leg and would be an unhedged short-beta bet; express the views on the BVLS-residual alpha (or optimise with a beta
and vol neutrality constraint) and expect lower payoff in rising markets. This is a diagnostic only: it changes neither N nor the MS2 decision.

### Pre-registration deviations (recorded, not corrected)
(a) Trials #19-20 (RF_T1_VSURP, RIDGE_T1_VSURP): their covered subset (`isna_vsurp == 0`) was injected by the driver from the gate key (`vsurp_validity`) and is not in the
hashed config, so the registered hash does not describe the data the trial was fit on.
(b) The champion ENS_RANKAVG_T has no CPCV: CPCV covers only SCR_T0, OLS_T0, RIDGE_T0 and the T_EW baseline (the ensemble would need CPCV of all 10 members including RF, MLP and SVR,
far beyond the 2.5 h budget); its evidence is the 138-anchor walk-forward only.


## MS1-core: 1Y bake-off (branch `phase-ms1-1y-bakeoff`, worktree `../wt-ms1`)

Date 2026-11-11. Snapshot `20261105-e33e7986` (features/universe/labels as built by P2/P3 on `main`). No migration
(030-039 unused: `oos_prediction`, `rank_calibration`, `model`, `wf_result` already exist). `ideation init-db` was never run
from the worktree. Not merged. Trials #44-45 (text) were NOT run and stay `registered` (MS1-text).

### Headline (read this first)
- **No model is promoted. The composite baseline ships (P1_COMP_EW), and it is itself weak.** The 90/10 pick is
  **P1_UNIFIED_XS** (a Phase-I valuation composite, not a learner): mean OOS rank IC **0.121** (CI90 0.070 .. 0.180, positive in
  5/5 counted years, 2020-2024). It passes gates 1, 2 and 3 but fails **gate 4** (DSR 0.813 < 0.90 at N = 50; PBO 0.139 passes)
  and **gate 5** (adversarial AUC on the model inputs 0.72 > 0.70 in 4 of 5 counted folds). So even the best trial does not clear
  the pre-registered bar, and the rule says the baseline ships. The baseline P1_COMP_EW has mean IC 0.029 (CI90 0.007 .. 0.056,
  4/5 positive) and fails gate 1 itself (0.029 < 0.03).
- **The learners add nothing.** All 7 learners x 3 specs, OLS, SCR and every RF variant have mean IC between -0.021 and +0.014
  (the three `_MOM` variants 0.026-0.029 and RF_GLOBAL_TRAIN2Y 0.008; CI90 straddles 0 in every case, 2-4 of 5 positive years). RF_GLOBAL 0.003 [-0.075, 0.074]; RF_GLOBAL_EXT -0.002; the 90/10-by-security
  variants 0.003 / -0.001; ENS_RANKAVG 0.005; CONS_MAJ -0.004. **RF fails** gates 1, 3 (dIC -0.026 vs the baseline, P(dIC>0) = 0.29),
  4 and 5. The dossier's RF values (0.223 / 0.233 / 0.25) are not reproduced and were not a target (different data, N, years).
- **The signal that exists is valuation.** The bench-1Y equal-weight VALUE_ONLY composite (ep, sp, ebitda_ev, fcf_yield, bp) has IC 0.129 in
  5/5 years, the three-yield P1_UNIFIED(_XS) 0.110 / 0.121, P1_FINAL_WFO (scr on the same three yields) 0.079. Learners trained on
  3 anchors lose the 2020 rebound (IC -0.15 to -0.18 for RF/LGBM/XGB/MLP/SVR/OLS/Ridge in Jun-2020, trained on 2017-2019) and are
  near 0 elsewhere except Jun-2023 (+0.10 to +0.15). The mean over 5 tests is dominated by that one year.
- Honest caveat: P1_UNIFIED(_XS) were selected among 50 pre-registered trials on the same 5 test years; the DSR (0.81) already
  deflates for that and it does not clear 0.90. Treat 0.121 as an upper bound, not an expectation.
- CPCV (36 splits, 8 paths, anchors 2016-2024, trains on both sides of the test group) tells a milder version: P1_UNIFIED_XS 0.069,
  P1_COMP_EW 0.060, RF_GLOBAL 0.022, **RF_GLOBAL_MOM 0.091, RIDGE_GLOBAL_MOM 0.095, RF_GLOBAL_EXT 0.051** (momentum/regime help
  once the training set is larger than 3 anchors; in WFO they do not: 0.029 / 0.026 / -0.002). CPCV is a robustness view and not
  the gate series.

### What was built
- `ideation/bakeoff/` (no lake or psycopg import except `persist.py` through the guarded writer):
  `data.py` (panel = U_score members x fundamental + technical + regime columns + labels read **only through `load_labels`**;
  `build_matrix` = R1 on the trial's whole universe at each anchor, then target = rank-gauss of `ret_total` within the trial
  universe), `models.py` (trial config -> model; scr gets priors, rf/ridge/etc. get the `BroadcastScaler` when the feature set has
  broadcast columns, 90/10-by-security gets the core columns only), `run.py` (WFO folds via `cv.walkforward.annual_folds`, CPCV via
  `cv.cpcv.cpcv_splits`, row-level C3 / span-overlap guards, clustered permutation importance per fold, **per-(trial, fold)
  parquet+json checkpoints** under `IDEATION_DATA_DIR/bakeoff/<snap>/<hash12>/`, resumable), `driver.py` (the loop; consensus
  trials from the members' fold/split scores; window variants copy their base's CPCV), `metrics.py` (R-A..R-C, name-bootstrap IC CI
  with 10,000 resamples, nested/pooled calibration + conformal, CPCV path/PBO inputs, consensus scores), `report.py` (DSR, paired
  dIC, 90/10 selection, gates, tables), `audit.py` (adversarial AUC, isolation-forest share), `finalize.py` (report + benchmarks),
  `persist.py` (DB), `cli.py`.
- CLI: `ideation train --horizon 1y --bakeoff --cpcv [--trials a,b] [--no-report]` (refuses an unregistered config hash) and
  `ideation backtest report [--no-persist] [--leakage-green]` (recomputes everything from stored results, no refit).
- `config/trials_1y.yaml` (owner MS1): the **hashed defaults now spell out** `feature_sets` (core 25; core_ext = core + size + rvol_63 +
  beta_252 + 17 regime broadcast columns; core_mom = core + 20 momentum / reversal / relative-strength / trend / breakout
  columns of `set: tech`; core_text), `universe_rules` and `protocol` (CPCV 9 groups / k 2 / embargo 1, importance repeats 5,
  bootstrap 10,000). They were added **before** `ideation trials register --family 1Y-june` (50 new rows, N = 50, hashes
  frozen), so nothing was changed after the first fit.
- Persisted (live `ideation` schema, normal run registry): `experiment_trial` 48 `ok` + 2 `registered` (status, n_oos, ic_series,
  spread_series, run 263), `model` 48 `candidate` rows (never `champion`: promotion is manual), `wf_result` 282 rows (6 anchors per
  June trial incl. 2019, 2 for POSTCOVID, 5 per March trial), `oos_prediction` 336,180 rows (WFO only; CPCV scores are in parquet,
  the table key allows one row per name), `rank_calibration` 5,880 rows (nested 2,590, pooled 3,290). Parquet copies in
  `IDEATION_DATA_DIR/oos/<trial_id>.parquet`; full report `IDEATION_DATA_DIR/reports/ms1/MS1_1Y_bakeoff_<snap>.md` (all 48 trials x R-A
  nested + pooled, R-B, R-C; 278 KB, not in git). The holdout was never read: `holdout_unseal` has 0 rows.
- Tests: `tests/leakage/test_ms1_bakeoff.py` (fold table = the C3 table exactly, panel never holds a sealed anchor, WFO resume without
  refit and identical scores, 36 CPCV splits + overlap check with teeth, consensus scores), `tests/unit/test_ms1_metrics.py` (4),
  **T-26** wired to the bake-off entry points (`load_panel`, `run_wfo`, `run_cpcv` on a synthetic source that contains 2025 anchors),
  **T-02** `tests/lake/test_ms1_lake.py`.

### Commands and runtimes
| command | result | runtime |
|---|---|---|
| `uv run ideation trials register --family 1Y-june` | 50 registered, N = 50 | 2 s |
| `uv run ideation train --horizon 1y --bakeoff --cpcv --trials <11 linear/Phase-I> --no-report` (smoke) | run 262 | 88 s |
| `nice -n 10 uv run ideation train --horizon 1y --bakeoff --cpcv --no-report` (run 263) | 48 trials ok, 0 failed; 264 WFO fits + 1,512 CPCV fits | **7,681 s wall (128 min)** |
| `uv run ideation backtest report --leakage-green` | tables, DSR, PBO, 90/10, gates, adversarial, persistence | 753 s (adversarial audits cached afterwards) |
Fit time only: WFO 283 s (card limit 20 min: met), CPCV 3,192 s = 53 min (limit 60: met). Wall time exceeds the card's 90 min because it
includes the clustered permutation importance (5 repeats per fold, SVR dominates: ~15 min per SVR trial), scoring, checkpoint I/O,
and sharing the Mac with MS2 at `nice -n 10`, 4 threads. Per trial wall: P1 linear 3-6 s, ridge 5-9 s, LGBM/XGB 35-55 s, RF 3-5.5 min,
MLP 4-5.5 min, SVR 16-21 min. The card number was not met in wall time; no statistic was cut to meet it.

### Protocol as implemented (decisions the plan left open)
- **Folds**: June anchors 2016-2024; WFO tests 2019 (indicative) + 2020-2024 (counted), train {T-3, T-2, T-1} (2019 <- 2016-2018 ... 2024 <-
  2021-2023; unit-tested against the C3 table). #7 and #38 use 2 anchors, #39 expands, #9 trains on anchors >= 2020-06-01 (tests 2023, 2024). March family:
  anchors 2016-03..2024-03, counted 2021-2024, indicative 2020.
- **Universe**: U_score; #42 = `in_u_score_adv05`; #43 U1000 = `in_u_score AND in_u1000` (a name needs a company and fundamentals to be scorable).
  R1 and the percentile p run on the **whole** trial universe at the anchor; unlabelled rows would only be dropped from fits and metrics
  (none occurred: every U_score name has a label at every June anchor 2016-2024).
- **Target**: rank-gauss of `ret_total` within the trial universe at each train anchor (not the stored `xs_rank`, which pools U_score).
- **CPCV** (9 groups = 9 anchors, k = 2, embargo 1; 36 splits, 8 paths): window variants (#7, #38, #39) have no CPCV analogue (CPCV trains
  on every non-purged anchor), so their CPCV is their base's (copied, listed as such) and they are left out of the PBO matrix; #9 uses its own
  anchor restriction; consensus trials are built per split from the members. March trials run CPCV on the March anchors and are not in the June PBO.
- **PBO**: matrix = path-averaged CPCV OOS IC per anchor (9 x trials; 42 trials, `CONS_UNAN` left out for an incomplete series); for each of the 36 splits
  OOS = mean over the 2 test anchors, IS = mean over the split's train anchors of the **same out-of-sample matrix**, never an in-sample fitted IC
  (high-capacity learners would always win otherwise). PBO = share of splits with logit <= 0.
- **DSR**: IC series of the 5 counted anchors (n_obs 5), per-trial Sharpe = mean/sd of IC, variance of Sharpe across the trials with >= 3 counted tests,
  **N = 50** (family count, all statuses). Also computed on the cost-adjusted Q5-Q1 spread.
- **90/10 (reading b)** = `0.9 z(mean OOS IC) + 0.1 z(stability)`, stability from the clustered permutation importance of consecutive folds (5 repeats, not 20),
  across the 40 eligible trials (June family, 5 counted tests, fitted: not #9, #40, #41, #46-#48).
- **Gates**: 02 §5.5 with O3. G1 mean IC >= 0.03, CI90 (iid bootstrap over the 5 anchors, 10,000) > 0, >= 4/5 positive. G2 mean Q5 `excess_univ_med` minus
  2 x 12 bps x Q5 turnover > 0 in >= 4/5 and on average (turnover vs the previous OOS anchor; the 2019 anchor assumes full turnover). G3 mean dIC vs P1_COMP_EW
  >= -0.01 and paired-bootstrap P(dIC>0) >= 0.6. G4 DSR >= 0.90 and PBO <= 0.5. G5 leakage suites green and adversarial AUC <= 0.70 on counted folds.
  **G2 as specified is skew-biased**: a quintile *mean* minus the universe *median* is positive for any random quintile, so every trial passes it (including
  RF with IC 0). The unbiased companion G2b (Q5 mean minus the equal-weight universe mean, net of costs) is reported next to it: it passes for P1_UNIFIED_XS and fails
  for the baseline and all RF variants. G2b is informational and not part of `all_pass`.
- **Adversarial validation** (02 §6.1, blocking): RF 200 trees depth 6, 5-fold CV, train vs test anchor, on the cross-sectional **value** columns of the model input;
  the verdict uses the 5 counted folds (2019 is reported). With the isna / na_layout flags it is within 0.01-0.02 of that; with the anchor-constant regime columns
  (RF_GLOBAL_EXT) it is 1.00 by construction (informational). Top shift features are the growth columns (rev_g1, eps_g1, rev_g3) and their missing flags: the share of
  zero-filled growth values and the joint structure shift between years, which a per-anchor rank-gauss cannot remove. 02 §6.1 prescribes dropping or re-transforming the top
  shift feature and logging the rerun as a **new trial**; that would raise N beyond the pre-registered 50, so it was not done. This is reported as a finding, not repaired.
- **Consensus trials**: CONS_MAJ score = number of the 10 members placing the name in D10 (votes), CONS_UNAN = 1 if all 10 do (anchor excluded, score NaN,
  when fewer than 5 names), ENS_RANKAVG = mean p. CONS_UNAN had >= 5 unanimous names at only 1 of 5 counted anchors, so its IC row is a single number.
- **R-A**: bucket means are per-anchor means averaged over the 5 counted anchors; nested alpha/band use counted anchors < T (the first counted anchor, 2020, has no
  history and 2019 is excluded), pooled uses all 5 (descriptive). Conformal bands are the 80% equal-tailed Mondrian bands of 05 §2 R6.
- **Benchmarks** (bench-1Y, reported only, not in N): computed from the same R1 matrix with equal weights.

### Results
## Selection (90/10 reading b; eligible = June-family fitted trials with 5 counted tests)

| trial | 0.9 z(IC) + 0.1 z(stability) | mean IC | stability |
|---|---|---|---|
| P1_UNIFIED_XS | 3.576 | 0.121 | 0.806 |
| P1_UNIFIED | 3.242 | 0.110 | 0.800 |
| P1_FINAL_WFO | 2.349 | 0.079 | 0.858 |
| P1_COMP_DOSSIER | 0.781 | 0.038 | 0.321 |
| P1_COMP_EW | 0.545 | 0.029 | 0.383 |
| RF_GLOBAL_MOM | 0.529 | 0.029 | 0.330 |
| P1_INDUSTRY_WF | 0.501 | 0.027 | 0.377 |
| RIDGE_GLOBAL_MOM | 0.501 | 0.026 | 0.433 |
| SCR_GLOBAL | 0.146 | 0.014 | 0.465 |
| LGBM_HYBRID | 0.035 | 0.011 | 0.403 |

#### Gates
## Gates (02 §5.5 with 05 O3)

Leakage tests green: yes (supplied by the runner). DSR uses N = 50.

| trial | G1_ic | G2_cost_adj_top_quintile | G2b_vs_EW_mean_info | G3_vs_baseline | G4_dsr_pbo | G5_leakage_adversarial | all_pass | dsr | pbo | adversarial | worst AUC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P1_UNIFIED_XS | yes | yes | yes | yes | no | no | no | 0.813 | 0.139 | BLOCK | 0.722 |
| P1_COMP_EW | no | yes | no | n/a | no | no | no | 0.152 | 0.139 | BLOCK | 0.722 |
| RF_GLOBAL | no | yes | no | no | no | no | no | 0.017 | 0.139 | BLOCK | 0.722 |
| RF_GLOBAL_EXT | no | yes | no | no | no | no | no | 0.013 | 0.139 | BLOCK | 0.728 |
| RF_GLOBAL_9010SEC | no | yes | no | no | no | no | no | 0.018 | 0.139 | BLOCK | 0.722 |
| RF_GLOBAL_EXT_9010SEC | no | yes | no | no | no | no | no | 0.013 | 0.139 | BLOCK | 0.728 |

DSR(IC) of the champion 0.813 (SR0 and variance in the report), P(dIC>0) vs the baseline 0.986. PBO and DSR use the whole family (N = 50).

#### RF lineage
## RF lineage (RF_GLOBAL -> RF_GLOBAL_EXT -> {9010SEC, EXT_9010SEC} -> 90/10 pick)

| step | mean IC | CI90 | reference (not target) |
|---|---|---|---|
| RF_GLOBAL | 0.003 | -0.075 .. 0.074 | 0.223 |
| RF_GLOBAL_EXT | -0.002 | -0.071 .. 0.059 | 0.233 |
| RF_GLOBAL_9010SEC | 0.003 | -0.074 .. 0.073 | 0.250 |
| RF_GLOBAL_EXT_9010SEC | -0.001 | -0.077 .. 0.060 | n/a |
| 90/10 pick: P1_UNIFIED_XS | 0.121 |  | 0.250 |

The dossier values (0.223 / 0.233 / 0.25) are on a different dataset, years and N; printed for reconciliation only.

#### Benchmarks (bench-1Y; levels are 1Y total returns Jun->Jun)
## Benchmarks (bench-1Y, reported only, not in N)

| benchmark | mean IC | pos | Q5 excess | Q5 excess net 12bps |
|---|---|---|---|---|
| MOM_12_1 | 0.012 | 3/5 | 16.5% | 16.3% |
| LOWVOL | 0.024 | 3/5 | 9.4% | 9.2% |
| VALUE_ONLY | 0.129 | 5/5 | 28.2% | 28.1% |
| QUALITY_ONLY | -0.022 | 2/5 | 7.2% | 7.1% |
| GROWTH_ONLY | 0.035 | 4/5 | 24.3% | 24.1% |

| anchor | universe median TR | Nifty 500 PR | EW U_score TR |
|---|---|---|---|
| 2020-06-30 | 81.3% | 57.5% | 110.7% |
| 2021-06-30 | -3.6% | -0.5% | 6.4% |
| 2022-06-30 | 21.7% | 22.7% | 36.2% |
| 2023-06-30 | 39.8% | 36.5% | 65.5% |
| 2024-06-28 | -5.0% | 3.9% | -0.1% |

#### PBO
## PBO (CPCV, 36 two-group combinations)

PBO = 0.139, trials in the matrix = 42, median logit = 1.924. Left out for incomplete anchor ICs: ['CONS_UNAN']. IS winners: {'RIDGE_GLOBAL_MOM': 18, 'P1_UNIFIED_XS': 10, 'RF_GLOBAL_MOM': 7, 'P1_COMP_EW': 1}

## Adversarial validation (champion's and RF_GLOBAL_EXT's inputs; the other focus trials share the matrix)

| trial | fold | AUC values | verdict | AUC +flags (info) | AUC +broadcast (info) | top shift features | OOD share |
|---|---|---|---|---|---|---|---|
| P1_UNIFIED_XS | 2019-06-28 | 0.81 | BLOCK | 0.81 | n/a | isna_rev_g3, isna_eps_g3, eps_g3 | 2.5% |
| P1_UNIFIED_XS | 2020-06-30 | 0.72 | BLOCK | 0.71 | n/a | isna_eps_g3, eps_g1, rev_g1 | 1.4% |
| P1_UNIFIED_XS | 2021-06-30 | 0.69 | warning | 0.71 | n/a | rev_g1, eps_g1, nd_ebitda | 0.7% |
| P1_UNIFIED_XS | 2022-06-30 | 0.71 | BLOCK | 0.72 | n/a | rev_g3, eps_g1, rev_g1 | 1.1% |
| P1_UNIFIED_XS | 2023-06-30 | 0.70 | BLOCK | 0.71 | n/a | rev_g3, eps_g1, rev_g1 | 1.1% |
| P1_UNIFIED_XS | 2024-06-28 | 0.72 | BLOCK | 0.72 | n/a | eps_g1, rev_g1, nd_ebitda | 0.7% |
| RF_GLOBAL_EXT | 2019-06-28 | 0.81 | BLOCK | 0.82 | 1.00 | isna_eps_g3, isna_rev_g3, eps_g3 | 3.4% |
| RF_GLOBAL_EXT | 2020-06-30 | 0.73 | BLOCK | 0.72 | 1.00 | isna_eps_g3, eps_g1, rev_g1 | 1.6% |
| RF_GLOBAL_EXT | 2021-06-30 | 0.69 | warning | 0.71 | 1.00 | eps_g1, nd_ebitda, eps_g3 | 1.0% |
| RF_GLOBAL_EXT | 2022-06-30 | 0.72 | BLOCK | 0.74 | 1.00 | rev_g3, eps_g1, eps_g3 | 0.9% |
| RF_GLOBAL_EXT | 2023-06-30 | 0.71 | BLOCK | 0.72 | 1.00 | rev_g3, eps_g1, eps_g3 | 1.5% |
| RF_GLOBAL_EXT | 2024-06-28 | 0.73 | BLOCK | 0.74 | 1.00 | eps_g1, nd_ebitda, rev_g1 | 0.6% |

OOD (isolation forest, P99 of the training scores): 0.7-3.4% of the test cross-section is flagged per fold, below the 10% "shifted" mark.

#### IC by anchor, key trials (Jun-2019 is indicative)
| trial | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | mean 2020-24 |
|---|---|---|---|---|---|---|---|
| P1_UNIFIED_XS | -0.170 | 0.253 | 0.071 | 0.116 | 0.133 | 0.034 | 0.121 |
| P1_FINAL_WFO | 0.007 | 0.124 | 0.014 | 0.113 | 0.105 | 0.041 | 0.079 |
| P1_COMP_EW (baseline) | 0.095 | 0.003 | 0.025 | 0.024 | -0.001 | 0.092 | 0.029 |
| RF_GLOBAL | 0.200 | -0.176 | 0.050 | -0.008 | 0.138 | 0.010 | 0.003 |
| RF_GLOBAL_EXT | 0.203 | -0.173 | 0.064 | 0.000 | 0.097 | 0.003 | -0.002 |
| RF_GLOBAL_MOM | 0.184 | -0.145 | 0.032 | 0.025 | 0.174 | 0.059 | 0.029 |
| LGBM_GLOBAL | 0.164 | -0.179 | 0.060 | -0.040 | 0.115 | 0.009 | -0.007 |
| XGB_GLOBAL | 0.159 | -0.178 | 0.037 | -0.032 | 0.105 | 0.020 | -0.010 |
| ENS_RANKAVG | 0.172 | -0.158 | 0.042 | -0.023 | 0.137 | 0.028 | 0.005 |

#### Tables R-D (all fitted trials), then R-A..R-C for the champion, the baseline and RF_GLOBAL
(The R-A/R-B/R-C tables of every other trial are in the report file named above.)

R-D (all fitted trials)

| # | trial | mean IC | CI90 | CI95 | pos | IC-IR | DSR(IC) | DSR(spread) | PBO | 90/10 | stab | CPCV mean IC | CPCV paths>0 | dIC vs base | P(dIC>0) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | P1_UNIFIED | 0.110 | 0.057 .. 0.170 | 0.053 .. 0.178 | 5/5 | 1.335 | 0.731 | 0.699 | 0.139 | 3.242 | 0.800 | 0.060 | 100.0% | 0.082 | 0.969 |
| 2 | P1_UNIFIED_XS | 0.121 | 0.070 .. 0.180 | 0.065 .. 0.193 | 5/5 | 1.456 | 0.813 | 0.626 | 0.139 | 3.576 | 0.806 | 0.069 | 100.0% | 0.093 | 0.986 |
| 3 | P1_COMP_DOSSIER | 0.038 | 0.015 .. 0.059 | 0.013 .. 0.062 | 4/5 | 1.177 | 0.553 | 0.109 | 0.139 | 0.781 | 0.321 | 0.054 | 100.0% | 0.009 | 0.693 |
| 4 | P1_COMP_EW | 0.029 | 0.007 .. 0.056 | 0.006 .. 0.061 | 4/5 | 0.764 | 0.152 | 0.004 | 0.139 | 0.545 | 0.383 | 0.060 | 100.0% | n/a | n/a |
| 5 | P1_INDUSTRY_WF | 0.027 | -0.042 .. 0.095 | -0.064 .. 0.112 | 4/5 | 0.250 | 0.056 | 0.020 | 0.139 | 0.501 | 0.377 | 0.022 | 87.5% | -0.001 | 0.455 |
| 6 | P1_WFO_3Y | -0.058 | -0.116 .. 0.000 | -0.116 .. 0.000 | 1/5 | -0.701 | n/a | n/a | 0.139 | n/a | 0.376 | 0.033 | 100.0% | -0.106 | 0.000 |
| 7 | P1_WFO_2Y | -0.063 | -0.102 .. -0.024 | -0.102 .. -0.024 | 0/5 | -1.134 | n/a | n/a | 0.139 | n/a | 0.500 | 0.033 | 100.0% | -0.111 | 0.000 |
| 8 | P1_FINAL_WFO | 0.079 | 0.047 .. 0.113 | 0.039 .. 0.116 | 5/5 | 1.624 | 0.775 | 0.788 | 0.139 | 2.349 | 0.858 | 0.060 | 100.0% | 0.051 | 0.941 |
| 9 | P1_POSTCOVID | 0.066 | 0.028 .. 0.105 | 0.028 .. 0.105 | 2/2 | 1.220 | n/a | n/a | 0.139 | n/a | 0.761 | 0.055 | 100.0% | 0.021 | 0.755 |
| 10 | OLS_GLOBAL | -0.004 | -0.068 .. 0.059 | -0.088 .. 0.076 | 3/5 | -0.038 | 0.012 | 0.007 | 0.139 | -0.389 | 0.451 | 0.014 | 100.0% | -0.032 | 0.221 |
| 11 | SCR_GLOBAL | 0.014 | -0.045 .. 0.073 | -0.055 .. 0.089 | 3/5 | 0.150 | 0.029 | 0.039 | 0.139 | 0.146 | 0.465 | 0.041 | 100.0% | -0.015 | 0.320 |
| 12 | RF_GLOBAL | 0.003 | -0.075 .. 0.074 | -0.097 .. 0.085 | 3/5 | 0.023 | 0.017 | 0.010 | 0.139 | -0.347 | 0.194 | 0.022 | 100.0% | -0.026 | 0.291 |
| 13 | RF_INDUSTRY | 0.006 | -0.073 .. 0.081 | -0.088 .. 0.087 | 3/5 | 0.051 | 0.021 | 0.003 | 0.139 | -0.136 | 0.385 | 0.021 | 100.0% | -0.023 | 0.317 |
| 14 | RF_HYBRID | 0.005 | -0.075 .. 0.079 | -0.096 .. 0.090 | 3/5 | 0.041 | 0.019 | 0.007 | 0.139 | -0.246 | 0.251 | 0.023 | 100.0% | -0.024 | 0.286 |
| 15 | PRIDGE_GLOBAL | 0.003 | -0.059 .. 0.063 | -0.073 .. 0.067 | 3/5 | 0.030 | 0.018 | 0.007 | 0.139 | -0.283 | 0.296 | 0.025 | 100.0% | -0.026 | 0.245 |
| 16 | PRIDGE_INDUSTRY | -0.021 | -0.096 .. 0.044 | -0.112 .. 0.053 | 3/5 | -0.200 | 0.003 | 0.000 | 0.139 | -0.965 | 0.361 | 0.003 | 50.0% | -0.050 | 0.094 |
| 17 | PRIDGE_HYBRID | -0.011 | -0.068 .. 0.036 | -0.084 .. 0.049 | 3/5 | -0.133 | 0.005 | 0.028 | 0.139 | -0.674 | 0.327 | 0.019 | 100.0% | -0.040 | 0.141 |
| 18 | RIDGE_GLOBAL | -0.002 | -0.069 .. 0.062 | -0.087 .. 0.077 | 3/5 | -0.019 | 0.013 | 0.012 | 0.139 | -0.335 | 0.442 | 0.015 | 100.0% | -0.031 | 0.244 |
| 19 | RIDGE_INDUSTRY | -0.002 | -0.082 .. 0.058 | -0.098 .. 0.065 | 4/5 | -0.022 | 0.012 | 0.001 | 0.139 | -0.324 | 0.484 | 0.001 | 50.0% | -0.031 | 0.216 |
| 20 | RIDGE_HYBRID | -0.009 | -0.075 .. 0.047 | -0.083 .. 0.057 | 2/5 | -0.102 | 0.007 | 0.001 | 0.139 | -0.631 | 0.319 | 0.024 | 100.0% | -0.038 | 0.179 |
| 21 | LGBM_GLOBAL | -0.007 | -0.086 .. 0.062 | -0.103 .. 0.073 | 3/5 | -0.062 | 0.010 | 0.001 | 0.139 | -0.637 | 0.193 | 0.026 | 100.0% | -0.036 | 0.247 |
| 22 | LGBM_INDUSTRY | 0.008 | -0.061 .. 0.072 | -0.068 .. 0.080 | 3/5 | 0.078 | 0.023 | 0.027 | 0.139 | -0.003 | 0.520 | 0.024 | 100.0% | -0.021 | 0.289 |
| 23 | LGBM_HYBRID | 0.011 | -0.058 .. 0.074 | -0.077 .. 0.087 | 3/5 | 0.110 | 0.029 | 0.006 | 0.139 | 0.035 | 0.403 | 0.030 | 100.0% | -0.017 | 0.328 |
| 24 | XGB_GLOBAL | -0.010 | -0.082 .. 0.057 | -0.099 .. 0.064 | 3/5 | -0.091 | 0.007 | 0.007 | 0.139 | -0.721 | 0.189 | 0.024 | 100.0% | -0.038 | 0.188 |
| 25 | XGB_INDUSTRY | -0.004 | -0.073 .. 0.063 | -0.086 .. 0.076 | 3/5 | -0.036 | 0.012 | 0.005 | 0.139 | -0.329 | 0.545 | 0.024 | 100.0% | -0.032 | 0.198 |
| 26 | XGB_HYBRID | 0.003 | -0.067 .. 0.072 | -0.081 .. 0.081 | 3/5 | 0.025 | 0.017 | 0.029 | 0.139 | -0.196 | 0.449 | 0.030 | 100.0% | -0.026 | 0.274 |
| 27 | MLP_GLOBAL | -0.011 | -0.076 .. 0.054 | -0.089 .. 0.064 | 3/5 | -0.108 | 0.008 | 0.000 | 0.139 | -0.770 | 0.157 | 0.012 | 75.0% | -0.039 | 0.182 |
| 28 | MLP_INDUSTRY | -0.008 | -0.076 .. 0.046 | -0.090 .. 0.053 | 3/5 | -0.088 | 0.007 | 0.000 | 0.139 | -0.559 | 0.381 | 0.013 | 75.0% | -0.037 | 0.151 |
| 29 | MLP_HYBRID | -0.001 | -0.070 .. 0.063 | -0.087 .. 0.070 | 2/5 | -0.015 | 0.014 | 0.007 | 0.139 | -0.353 | 0.393 | 0.019 | 100.0% | -0.030 | 0.255 |
| 30 | SVR_GLOBAL | -0.007 | -0.083 .. 0.063 | -0.101 .. 0.069 | 3/5 | -0.067 | 0.010 | 0.007 | 0.139 | -0.644 | 0.193 | 0.020 | 100.0% | -0.036 | 0.190 |
| 31 | SVR_INDUSTRY | -0.013 | -0.080 .. 0.049 | -0.096 .. 0.054 | 2/5 | -0.131 | 0.005 | 0.000 | 0.139 | -0.592 | 0.561 | 0.012 | 75.0% | -0.041 | 0.127 |
| 32 | SVR_HYBRID | 0.002 | -0.066 .. 0.062 | -0.079 .. 0.065 | 3/5 | 0.023 | 0.018 | 0.017 | 0.139 | -0.185 | 0.486 | 0.024 | 100.0% | -0.026 | 0.243 |
| 33 | RF_GLOBAL_EXT | -0.002 | -0.071 .. 0.059 | -0.091 .. 0.065 | 4/5 | -0.017 | 0.013 | 0.003 | 0.139 | -0.410 | 0.314 | 0.051 | 100.0% | -0.030 | 0.228 |
| 34 | RF_GLOBAL_MOM | 0.029 | -0.043 .. 0.098 | -0.070 .. 0.116 | 4/5 | 0.256 | 0.059 | 0.043 | 0.139 | 0.529 | 0.330 | 0.091 | 100.0% | 0.001 | 0.503 |
| 35 | LGBM_GLOBAL_MOM | -0.000 | -0.078 .. 0.068 | -0.091 .. 0.077 | 3/5 | -0.003 | 0.015 | 0.003 | 0.139 | -0.329 | 0.377 | 0.070 | 100.0% | -0.029 | 0.248 |
| 36 | RIDGE_GLOBAL_MOM | 0.026 | -0.047 .. 0.108 | -0.055 .. 0.124 | 2/5 | 0.218 | 0.029 | 0.035 | 0.139 | 0.501 | 0.433 | 0.095 | 100.0% | -0.002 | 0.418 |
| 37 | RF_GLOBAL_DMEDIAN | 0.004 | -0.073 .. 0.077 | -0.097 .. 0.089 | 3/5 | 0.035 | 0.018 | 0.016 | 0.139 | -0.281 | 0.236 | 0.024 | 100.0% | -0.025 | 0.311 |
| 38 | RF_GLOBAL_TRAIN2Y | 0.008 | -0.086 .. 0.088 | -0.110 .. 0.098 | 4/5 | 0.061 | 0.023 | 0.007 | 0.139 | -0.033 | 0.445 | 0.022 | 100.0% | -0.021 | 0.410 |
| 39 | RF_GLOBAL_EXPANDING | 0.001 | -0.073 .. 0.047 | -0.080 .. 0.050 | 4/5 | 0.010 | 0.016 | 0.000 | 0.139 | -0.268 | 0.414 | 0.022 | 100.0% | -0.028 | 0.223 |
| 40 | RF_GLOBAL_MARCH | -0.020 | -0.080 .. 0.039 | -0.098 .. 0.061 | 1/4 | -0.226 | 0.013 | 0.046 | 0.139 | n/a | 0.487 | -0.020 | 25.0% | n/a | n/a |
| 41 | P1_COMP_EW_MARCH | -0.003 | -0.075 .. 0.058 | -0.076 .. 0.068 | 2/4 | -0.038 | 0.025 | 0.002 | 0.139 | n/a | 0.580 | 0.041 | 100.0% | n/a | n/a |
| 42 | RF_GLOBAL_ADV05 | 0.004 | -0.078 .. 0.085 | -0.094 .. 0.092 | 2/5 | 0.030 | 0.018 | 0.014 | 0.139 | -0.323 | 0.185 | 0.020 | 100.0% | -0.025 | 0.301 |
| 43 | RF_GLOBAL_U1000 | -0.003 | -0.100 .. 0.099 | -0.106 .. 0.107 | 2/5 | -0.022 | 0.014 | 0.022 | 0.139 | -0.510 | 0.219 | 0.016 | 87.5% | -0.032 | 0.299 |
| 46 | CONS_MAJ | -0.004 | -0.067 .. 0.065 | -0.076 .. 0.082 | 2/5 | -0.036 | 0.013 | n/a | 0.139 | n/a | n/a | -0.003 | 25.0% | -0.032 | 0.272 |
| 47 | CONS_UNAN | -0.001 | -0.001 .. -0.001 | -0.001 .. -0.001 | 0/5 | n/a | n/a | n/a | 0.139 | n/a | n/a | 0.011 | 37.5% | -0.025 | 0.000 |
| 48 | ENS_RANKAVG | 0.005 | -0.067 .. 0.075 | -0.083 .. 0.083 | 3/5 | 0.048 | 0.020 | 0.018 | 0.139 | n/a | n/a | 0.033 | 100.0% | -0.023 | 0.289 |
| 49 | RF_GLOBAL_9010SEC | 0.003 | -0.074 .. 0.073 | -0.100 .. 0.084 | 3/5 | 0.030 | 0.018 | 0.008 | 0.139 | -0.327 | 0.188 | 0.030 | 100.0% | -0.025 | 0.291 |
| 50 | RF_GLOBAL_EXT_9010SEC | -0.001 | -0.077 .. 0.060 | -0.095 .. 0.064 | 4/5 | -0.013 | 0.013 | 0.002 | 0.139 | -0.404 | 0.304 | 0.057 | 100.0% | -0.030 | 0.265 |

P1_UNIFIED_XS: R-A nested

| bucket | score range (latest) | p range | IS range | mean n | mean excess | median excess | hit | beat N500 | beat EW | alpha_iso (latest) | 80% band (latest) | coverage | anchors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P99 | 5.422 .. 7.658 | 0.99 .. 1.00 | 99 .. 100 | 13 | 17.5% | 10.6% | 47.8% | 51.5% | 44.7% | 16.9% | -44.2% .. 131.6% | 79.9% | 5 |
| P95 | 3.912 .. 7.658 | 0.95 .. 1.00 | 95 .. 100 | 63 | 36.4% | 15.2% | 58.2% | 56.6% | 45.1% | 16.9% | -44.2% .. 131.6% | 85.3% | 5 |
| D10 | 2.691 .. 7.658 | 0.90 .. 1.00 | 90 .. 100 | 127 | 28.8% | 12.5% | 55.6% | 53.8% | 43.6% | 16.9% | -44.2% .. 131.6% | 85.1% | 5 |
| D9 | 1.622 .. 2.677 | 0.80 .. 0.90 | 80 .. 90 | 127 | 30.5% | 13.7% | 56.0% | 57.0% | 45.0% | 16.9% | -41.6% .. 119.6% | 86.3% | 5 |
| D8 | 0.780 .. 1.620 | 0.70 .. 0.80 | 70 .. 80 | 127 | 29.4% | 7.9% | 55.1% | 54.8% | 43.3% | 16.9% | -34.7% .. 119.2% | 90.3% | 5 |
| D7 | 0.077 .. 0.778 | 0.60 .. 0.70 | 60 .. 70 | 127 | 15.6% | 0.5% | 50.1% | 51.5% | 37.1% | 9.5% | -43.3% .. 88.3% | 86.4% | 5 |
| D6 | 0.000 .. 0.075 | 0.53 .. 0.60 | 53 .. 60 | 168 | 13.5% | 0.3% | 51.9% | 51.1% | 39.3% | 9.5% | -42.5% .. 101.4% | 87.4% | 5 |
| D5 | -0.419 .. -0.000 | 0.40 .. 0.47 | 40 .. 47 | 85 | 27.2% | 3.9% | 51.3% | 50.5% | 40.0% | 9.5% | -44.6% .. 106.7% | 84.1% | 5 |
| D4 | -1.083 .. -0.420 | 0.30 .. 0.40 | 30 .. 40 | 127 | 12.4% | -0.9% | 48.5% | 47.8% | 36.5% | 7.2% | -46.2% .. 90.1% | 90.1% | 5 |
| D3 | -1.665 .. -1.085 | 0.20 .. 0.30 | 20 .. 30 | 127 | 10.0% | -2.0% | 46.4% | 47.6% | 34.3% | 5.7% | -50.7% .. 88.3% | 87.8% | 5 |
| D2 | -2.528 .. -1.670 | 0.10 .. 0.20 | 10 .. 20 | 127 | 5.5% | -7.7% | 42.8% | 44.0% | 31.2% | 3.1% | -53.2% .. 77.4% | 87.4% | 5 |
| D1 | -6.033 .. -2.549 | 0.00 .. 0.10 | 0 .. 10 | 127 | 2.1% | -12.3% | 38.3% | 39.2% | 28.3% | 1.8% | -56.0% .. 77.5% | 84.0% | 5 |
| P5 | -6.033 .. -3.179 | 0.00 .. 0.05 | 0 .. 5 | 63 | 0.6% | -15.3% | 35.3% | 35.5% | 26.7% | 1.8% | -56.2% .. 77.4% | 80.0% | 5 |
| P1 | -6.033 .. -4.614 | 0.00 .. 0.01 | 0 .. 1 | 13 | 18.8% | 1.5% | 35.3% | 41.6% | 34.0% | 1.8% | -56.2% .. 77.4% | 73.4% | 5 |

P1_UNIFIED_XS: R-B

| anchor | set | IC | CI90 (names) | n scored | coverage | D10-D1 | Q5-Q1 | Q5-Q1 net 12bps | Q5 excess net |
|---|---|---|---|---|---|---|---|---|---|
| 2019-06-28 | indicative | -0.170 | -0.222 .. -0.116 | 863 | 100.0% | -12.6% | -21.8% | -22.3% | -1.7% |
| 2020-06-30 | counted | 0.253 | 0.199 .. 0.305 | 857 | 100.0% | 71.8% | 68.4% | 68.3% | 67.3% |
| 2021-06-30 | counted | 0.071 | 0.022 .. 0.119 | 1172 | 100.0% | -1.5% | 8.6% | 8.3% | 16.2% |
| 2022-06-30 | counted | 0.116 | 0.070 .. 0.162 | 1303 | 100.0% | 20.9% | 20.0% | 19.8% | 17.1% |
| 2023-06-30 | counted | 0.133 | 0.088 .. 0.177 | 1409 | 100.0% | 37.8% | 27.5% | 27.3% | 40.6% |
| 2024-06-28 | counted | 0.034 | -0.009 .. 0.076 | 1588 | 100.0% | 4.2% | 4.5% | 4.3% | 6.4% |

P1_UNIFIED_XS: R-C

| anchor | Spearman(decile, mean ret) | P99>P95>D10>D9 | P1<P5<D1<D2 |
|---|---|---|---|
| 2020-06-30 | 0.697 | no | no |
| 2021-06-30 | 0.333 | no | no |
| 2022-06-30 | 0.661 | no | yes |
| 2023-06-30 | 0.879 | yes | no |
| 2024-06-28 | 0.358 | no | no |
| count of 5 | 0.585 | 1 | 1 |

P1_COMP_EW: R-A nested

| bucket | score range (latest) | p range | IS range | mean n | mean excess | median excess | hit | beat N500 | beat EW | alpha_iso (latest) | 80% band (latest) | coverage | anchors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P99 | 0.587 .. 0.696 | 0.99 .. 1.00 | 99 .. 100 | 13 | 10.1% | -12.7% | 43.2% | 41.4% | 34.1% | 9.9% | -44.4% .. 102.5% | 85.4% | 5 |
| P95 | 0.456 .. 0.696 | 0.95 .. 1.00 | 95 .. 100 | 63 | 12.4% | -0.3% | 48.8% | 48.1% | 34.3% | 9.9% | -44.4% .. 102.5% | 90.6% | 5 |
| D10 | 0.381 .. 0.696 | 0.90 .. 1.00 | 90 .. 100 | 127 | 16.7% | 4.2% | 51.4% | 50.4% | 38.8% | 9.9% | -44.4% .. 102.5% | 88.3% | 5 |
| D9 | 0.289 .. 0.381 | 0.80 .. 0.90 | 80 .. 90 | 127 | 12.5% | -0.3% | 47.7% | 47.0% | 35.9% | 9.9% | -48.0% .. 99.6% | 89.3% | 5 |
| D8 | 0.210 .. 0.289 | 0.70 .. 0.80 | 70 .. 80 | 127 | 19.0% | 3.1% | 51.0% | 50.5% | 40.1% | 9.9% | -45.1% .. 113.1% | 86.5% | 5 |
| D7 | 0.142 .. 0.210 | 0.60 .. 0.70 | 60 .. 70 | 127 | 16.6% | 2.6% | 53.0% | 53.0% | 39.2% | 9.9% | -38.9% .. 85.7% | 86.4% | 5 |
| D6 | 0.077 .. 0.141 | 0.50 .. 0.60 | 50 .. 60 | 127 | 16.4% | -1.2% | 49.4% | 51.7% | 38.2% | 9.9% | -45.2% .. 112.5% | 88.2% | 5 |
| D5 | -0.007 .. 0.077 | 0.40 .. 0.50 | 40 .. 50 | 126 | 16.5% | 3.4% | 53.6% | 51.9% | 38.0% | 9.9% | -46.7% .. 81.8% | 86.5% | 5 |
| D4 | -0.110 .. -0.008 | 0.30 .. 0.40 | 30 .. 40 | 127 | 16.7% | -0.9% | 48.4% | 48.2% | 38.8% | 9.9% | -43.7% .. 107.6% | 89.7% | 5 |
| D3 | -0.245 .. -0.111 | 0.20 .. 0.30 | 20 .. 30 | 127 | 20.8% | 0.1% | 49.3% | 50.0% | 38.3% | 9.9% | -45.2% .. 115.2% | 86.2% | 5 |
| D2 | -0.487 .. -0.248 | 0.10 .. 0.20 | 10 .. 20 | 127 | 18.0% | -4.4% | 44.7% | 46.6% | 33.7% | 9.9% | -48.5% .. 117.6% | 87.7% | 5 |
| D1 | -1.541 .. -0.488 | 0.00 .. 0.10 | 0 .. 10 | 127 | 15.7% | -1.4% | 47.2% | 47.7% | 36.9% | 8.3% | -55.1% .. 107.0% | 86.5% | 5 |
| P5 | -1.541 .. -0.728 | 0.00 .. 0.05 | 0 .. 5 | 63 | 12.8% | -3.5% | 46.0% | 45.3% | 34.9% | 8.3% | -55.3% .. 106.8% | 85.5% | 5 |
| P1 | -1.541 .. -1.134 | 0.00 .. 0.01 | 0 .. 1 | 13 | 12.7% | -8.9% | 40.5% | 36.9% | 36.0% | 8.3% | -55.3% .. 106.8% | 76.9% | 5 |

P1_COMP_EW: R-B

| anchor | set | IC | CI90 (names) | n scored | coverage | D10-D1 | Q5-Q1 | Q5-Q1 net 12bps | Q5 excess net |
|---|---|---|---|---|---|---|---|---|---|
| 2019-06-28 | indicative | 0.095 | 0.039 .. 0.150 | 863 | 100.0% | -8.5% | 2.6% | 2.1% | 5.8% |
| 2020-06-30 | counted | 0.003 | -0.054 .. 0.061 | 857 | 100.0% | 2.5% | -2.6% | -2.7% | 30.5% |
| 2021-06-30 | counted | 0.025 | -0.023 .. 0.073 | 1172 | 100.0% | -3.0% | -5.3% | -5.6% | 6.4% |
| 2022-06-30 | counted | 0.024 | -0.024 .. 0.071 | 1303 | 100.0% | 16.6% | 4.2% | 3.9% | 11.0% |
| 2023-06-30 | counted | -0.001 | -0.046 .. 0.043 | 1409 | 100.0% | -10.8% | -12.1% | -12.4% | 17.1% |
| 2024-06-28 | counted | 0.092 | 0.049 .. 0.135 | 1588 | 100.0% | -0.3% | 4.6% | 4.4% | 7.4% |

P1_COMP_EW: R-C

| anchor | Spearman(decile, mean ret) | P99>P95>D10>D9 | P1<P5<D1<D2 |
|---|---|---|---|
| 2020-06-30 | -0.200 | no | yes |
| 2021-06-30 | -0.455 | yes | no |
| 2022-06-30 | -0.212 | no | yes |
| 2023-06-30 | -0.176 | no | no |
| 2024-06-28 | 0.455 | no | no |
| count of 5 | -0.118 | 1 | 2 |

RF_GLOBAL: R-A nested

| bucket | score range (latest) | p range | IS range | mean n | mean excess | median excess | hit | beat N500 | beat EW | alpha_iso (latest) | 80% band (latest) | coverage | anchors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P99 | 0.417 .. 0.582 | 0.99 .. 1.00 | 99 .. 100 | 13 | -1.8% | -15.3% | 38.0% | 33.9% | 20.6% | 10.1% | -41.0% .. 113.1% | 88.7% | 5 |
| P95 | 0.321 .. 0.582 | 0.95 .. 1.00 | 95 .. 100 | 63 | 3.5% | -10.0% | 41.8% | 39.2% | 27.6% | 10.1% | -41.0% .. 113.1% | 86.1% | 5 |
| D10 | 0.255 .. 0.582 | 0.90 .. 1.00 | 90 .. 100 | 127 | 13.1% | -3.8% | 47.6% | 47.1% | 33.6% | 10.1% | -41.0% .. 113.1% | 85.3% | 5 |
| D9 | 0.164 .. 0.255 | 0.80 .. 0.90 | 80 .. 90 | 127 | 12.7% | 0.1% | 50.9% | 49.7% | 37.1% | 10.1% | -41.9% .. 104.2% | 85.2% | 5 |
| D8 | 0.103 .. 0.164 | 0.70 .. 0.80 | 70 .. 80 | 127 | 14.6% | 0.4% | 50.5% | 51.4% | 38.6% | 10.1% | -47.7% .. 105.0% | 88.8% | 5 |
| D7 | 0.053 .. 0.102 | 0.60 .. 0.70 | 60 .. 70 | 127 | 19.0% | 3.6% | 51.9% | 50.1% | 40.0% | 10.1% | -47.0% .. 108.5% | 87.8% | 5 |
| D6 | -0.002 .. 0.053 | 0.50 .. 0.60 | 50 .. 60 | 127 | 21.0% | 5.2% | 53.4% | 52.6% | 39.7% | 10.1% | -42.4% .. 101.9% | 88.7% | 5 |
| D5 | -0.052 .. -0.002 | 0.40 .. 0.50 | 40 .. 50 | 126 | 20.4% | 2.2% | 51.1% | 52.3% | 39.6% | 10.1% | -42.0% .. 89.0% | 88.9% | 5 |
| D4 | -0.107 .. -0.052 | 0.30 .. 0.40 | 30 .. 40 | 127 | 12.3% | -2.2% | 47.1% | 46.7% | 34.5% | 9.1% | -50.3% .. 94.1% | 86.7% | 5 |
| D3 | -0.163 .. -0.108 | 0.20 .. 0.30 | 20 .. 30 | 127 | 20.4% | -3.8% | 45.5% | 47.4% | 37.5% | 9.1% | -44.4% .. 106.8% | 88.6% | 5 |
| D2 | -0.258 .. -0.165 | 0.10 .. 0.20 | 10 .. 20 | 127 | 11.3% | -3.4% | 44.5% | 46.2% | 34.0% | 9.1% | -56.8% .. 87.7% | 87.9% | 5 |
| D1 | -0.661 .. -0.259 | 0.00 .. 0.10 | 0 .. 10 | 127 | 24.1% | 8.6% | 53.3% | 53.5% | 43.4% | 9.1% | -47.7% .. 108.6% | 88.2% | 5 |
| P5 | -0.661 .. -0.341 | 0.00 .. 0.05 | 0 .. 5 | 63 | 31.8% | 17.0% | 59.9% | 60.9% | 51.1% | 9.1% | -47.7% .. 108.6% | 91.4% | 5 |
| P1 | -0.661 .. -0.460 | 0.00 .. 0.01 | 0 .. 1 | 13 | 33.0% | 17.4% | 64.5% | 63.4% | 56.4% | 9.1% | -47.7% .. 108.6% | 91.2% | 5 |

RF_GLOBAL: R-B

| anchor | set | IC | CI90 (names) | n scored | coverage | D10-D1 | Q5-Q1 | Q5-Q1 net 12bps | Q5 excess net |
|---|---|---|---|---|---|---|---|---|---|
| 2019-06-28 | indicative | 0.200 | 0.144 .. 0.255 | 863 | 100.0% | 10.1% | 9.3% | 8.8% | 10.5% |
| 2020-06-30 | counted | -0.176 | -0.231 .. -0.121 | 857 | 100.0% | -70.1% | -48.7% | -48.9% | -2.7% |
| 2021-06-30 | counted | 0.050 | 0.001 .. 0.099 | 1172 | 100.0% | -0.4% | -3.1% | -3.4% | 5.0% |
| 2022-06-30 | counted | -0.008 | -0.054 .. 0.039 | 1303 | 100.0% | -8.9% | 0.7% | 0.4% | 16.0% |
| 2023-06-30 | counted | 0.138 | 0.094 .. 0.182 | 1409 | 100.0% | 20.2% | 26.3% | 25.9% | 40.0% |
| 2024-06-28 | counted | 0.010 | -0.032 .. 0.051 | 1588 | 100.0% | 3.8% | 0.5% | 0.3% | 5.5% |

RF_GLOBAL: R-C

| anchor | Spearman(decile, mean ret) | P99>P95>D10>D9 | P1<P5<D1<D2 |
|---|---|---|---|
| 2020-06-30 | -0.818 | no | no |
| 2021-06-30 | -0.079 | no | no |
| 2022-06-30 | 0.382 | no | no |
| 2023-06-30 | 0.709 | no | no |
| 2024-06-28 | -0.091 | no | no |
| count of 5 | 0.021 | 0 | 0 |


### Acceptance
| item | result |
|---|---|
| all runnable trials ok/failed | 48 ok, 0 failed; #44-#45 left `registered` by design |
| `wf_result` + `oos_prediction` for 2019 (indicative) and 2020-2024 | yes (282 / 336,180 rows) |
| CPCV 36 splits for every runnable trial | yes (window variants copy their base: 3 trials) |
| DSR, PBO, 90/10 printed; gates evaluated | yes; champion P1_UNIFIED_XS; composite baseline ships |
| T-02 future-poison scores, Jun-2021 | pass: inflate / nan / shuffle poison of everything with `available_from` > 2021-06-30; universe (with the feature-complete hook), fundamental + technical + regime features, R1, fit and score all rebuilt from the poisoned store; frames identical (`check_exact`); scores bit-identical for RF_GLOBAL, RF_GLOBAL_EXT, P1_COMP_EW; RIDGE_GLOBAL agrees to 1e-12 with identical ranking (differs by ~1e-16 between two calls on identical inputs: BLAS summation depends on array alignment, so the Ridge assertion is `allclose`, not bitwise); positive control (poison 900 days earlier) changes the inputs |
| T-26 | green with the bake-off entry points wired in (the test source contains 2025 anchors that never reach a fit, a score call or the loader) |
| T-04 (shuffled labels, every learner) | E1's synthetic test; unchanged |
| runtime | fit time within the card (WFO 283 s, CPCV 53 min); wall 128 min (see above) |
| `uv run ruff check .` | clean |
| `uv run pytest -m "not lake"` | **563 passed**, 1 xfailed, 1 xpassed (E1 documented), 125 s |
| `uv run pytest -m lake` | **106 passed** (incl. T-02, 4 tests), 545 s |
| `uv run python scripts/secret_scan.py --all` | clean |

### Deviations from 05 and why
- **Wall time** over the card (above). **Importance repeats 5, not 20** (02 §6.5): 20 repeats would have made SVR the dominant cost (about 1 h per SVR trial); the stability term is 10% of the
  selection score and the champion's lead is 0.9 x IC. The value is in the hashed protocol block.
- **Window variants** have a copied CPCV (above); `P1_WFO_3Y` / `P1_WFO_2Y` have empty folds (selection found nothing, score NaN): 2 of 6 and 4 of 6 folds, reported, not relaxed.
  Their R-D means are over the non-empty counted folds and an empty fold counts as not positive.
- **G2 bias** and the **adversarial remedy** (above). H (Friedman) and ALE audits were not run; the OOD share is reported for the focus trials only.
- **U1000** and the **momentum set** are my readings (above); both are in the hashed config.
- **The lgbm/xgb weak-signal attenuation** (E1 T-05) is not fixed and not visible separately here: all boosters are near 0 like every other learner.
- MLP hits `max_iter=50` without converging (ConvergenceWarning recorded per trial; not suppressed); SVR subsamples to 6,000 rows by design.
- Test change outside MS1: `tests/lake/test_e1_ddl.py::test_register_then_train_guard` assumed an empty trial registry; with 1Y-june registered live it now counts inserted + already-present
  (still inside the rolled-back sandbox).
- `results_dir` moved from `<data>/<snap>/results` to `<data>/bakeoff/<snap>/results` during the run (a symlink bridged the live process; the directory was renamed after it exited).

### Risks
- **Five counted annual tests** cannot support DSR >= 0.90 for any realistic signal; the pre-registered expectation in 05 §3.7 held. Jun-2020 (COVID rebound) is one year and decides the sign of every learner's mean.
- Anything selected on these 5 years (P1_UNIFIED_XS, the VALUE composite) is optimistic. The **holdout (Jun-2025) is still sealed**; re-selecting after unsealing requires a new family.
- The adversarial BLOCK applies to the baseline and to every model sharing the R1 core matrix: the inputs themselves drift (growth-feature coverage by year), not just the learners.
- Sector/industry labels, the stock-to-company link and listing dates are today's (P1/P2 residual bias); text and vendor features are not in any trial here.
- Live-state footprint: 50 `experiment_trial` rows, 2 train runs (262 smoke, 263 full), 48 `model` rows, 282 `wf_result`, 336,180 `oos_prediction`, 5,880 `rank_calibration` rows. The orchestrator
  should re-run `ideation backtest report --leakage-green` on `main` after merge only if the snapshot changes; results are keyed by config hash and snapshot.
- MS1-text still owes #44-#45; `N` stays 50.

## Outcomes (branch `phase-outcomes`, from `main` ff83ea4; 05 §5, §10.2.c)

Date 2026-11-11. Snapshot `20261105-e33e7986`. No migration: `ideation.outcome` already exists (E1 012; verified, 0 rows before
the build). `init-db` never run from the worktree. Rows are keyed on the (anchor, security) pairs that have labels, read only
through `load_labels(..., "report")` after `unsealed_only`.

### What was built
- `ideation/outcomes/analyst.py` (second and last `BAN_ALLOWLIST` entry; emits `out_beat`, `out_surp`, `n_events`), `fundamental.py`
  (`out_fs_sales`, `out_fs_pat`, `out_accel`), `cli.py` (`ideation outcomes build|check`, one `add_typer` line in `ideation/cli.py`).
- Analyst: late-view `earnings_vendor()`; rows with `available_from != event_date + 1`, a missing estimate or a missing reported EPS are
  dropped (0 mismatches on the real data). `surp` and `beat` exactly as 05 §5.1. 1Y = first 4 events in (A, A+12M], 3M = first event in
  (A, A+3M].
- Fundamental: FY(Y) = latest FY first available by A (<= 550 days old); FY(Y+1) must land by A+12M+60d; basis rule per FY; growth or
  CAGR across a basis switch or a gap other than 12 months is NaN; PAT > 0 at both CAGR ends; values winsorised at +-5.
  `fundamental_q` (3M, anchors >= 2023-06): next-quarter YoY sales growth minus the median YoY of the latest 4 available quarters (>= 2 valid).
- Set statistics for the report layer: `analyst.compare_sets` (difference vs the rest, anchor-clustered bootstrap CI) and
  `fundamental.cmh_accel` (Cochran-Mantel-Haenszel by anchor). Unit-tested on synthetic data.
- Static tests: `BAN_ALLOWLIST` = {events_vendor.py, outcomes/analyst.py} with non-vacuous tests for both, `outcomes` added to the ban and
  clock scans, outcomes may not import models/rank/cv/lake/psycopg and may write only `ideation.outcome` (planted self-tests).

### Commands, runtimes, results
| Command | Result | Wall |
|---|---|---|
| `nice -n 10 uv run ideation outcomes build` | 82,292 rows (see below) | 65 s |
| `uv run ideation outcomes check` | the owner check below | 95 s |

Rows in `ideation.outcome`: 1Y analyst 5,068 (9 June anchors 2016-2024); 1Y fundamental 9,397 (9); 3M analyst 58,259 (138 monthly
anchors 2014-01..2025-06); 3M fundamental_q 9,568 (24 anchors, 2023-06..2025-06).
Coverage of U_score (June, analyst): 66%, 71%, 65%, 64%, 48%, 34%, 37%, 36%, 36% for 2016..2024 (later years are inside the expected
30-45%; earlier years are higher because U_score was small and large caps dominate). Fundamental 1Y coverage 89-93% for 2018-2024 but
2.8% (2016) and 3.4% (2017): broad annual history starts at FY2015, so FY(Y-3) is missing. `fundamental_q` covers 0.7% (2023), 10% (2024),
52% (2025) of U_score_T: quarterly breadth, as 03 §B3 warned.

**Owner check: does the surprise sign agree with the announcement return?** Events 2015-01-01..2025-06-30, n = 19,051, 42 quarters:
mean `ear_3d` is +1.74% after a beat and -1.52% after a miss (difference 3.26 pp), quarter-clustered (CR1) **t = 39.8**; sign agreement
61.7%; pooled Spearman 0.318, t = 37.5 (same events and number as the PT2 vendor gate).

Tests: `tests/unit/test_outcomes.py` (12: formulas, windows, announcement availability = event_date + 1, fundamental PIT rules,
quarterly rule, check and set statistics), `tests/lake/test_outcomes_lake.py` (7: availability pin, analyst and fundamental
future-poison in three modes with positive controls).

### Deviations from 05 and why
- Set comparison (P95, D10, C_maj, ...) and the D10-vs-D1 CMH run are not executed: they need the MS1/MS3 prediction sets, which are
  being built concurrently. The functions exist and are tested; hook them to `oos_prediction` after MS1 merges.
- Monthly anchors get horizon 3M only (no monthly 1Y, no 1M rows); `fundamental` is June-only.
- The 3M window of the 2025-06 anchor reads vendor events up to 2025-09-30, inside the sealed 3M label window (outcome rows of an
  unsealed anchor, as instructed); no sealed label is read.
- A (company, month) panel duplicate keeps the earliest-available row.

### Risks
- The snapshot holds one version per fact (no restatements), so "as restated in Nov-2026" is the same as first reported here.
- Vendor estimates cannot be shown to be point-in-time; coverage is survivors-only and size-biased (see PT2).
- Fundamental outcomes use the FY basis decided on the late view (hindsight on C/S choice); outcome-only.
- Quarterly outcomes are thin before 2025; treat `fundamental_q` as report-only.
