# Build status

| Phase | Status | Notes |
|---|---|---|
| Plan | done | docs/plan/00–03 |
| P0 scaffold | done (branch `phase-0-scaffold`) | see "Phase 0" below |
| P1 snapshot / universes / labels | done (branch `phase-1-pit-universe-labels`, not pushed) | see "Phase 1" below |

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
