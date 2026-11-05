# Build status

| Phase | Status | Notes |
|---|---|---|
| Plan | done | docs/plan/00–03 |
| P0 scaffold | done (branch `phase-0-scaffold`) | see "Phase 0" below |
| E1 eval stack + zoo | done on `phase-e1-eval-zoo` (synthetic only; one known gap, see E1) | see "E1" below |

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
| T-05 planted Spearman 0.10 (12 anchors × 800 names × 12 features; annual WF with refits, 9 test years; seed 0; oracle IC of f0 on the test anchors 0.099) | ridge 0.087 [CI90 0.057, 0.111], scr 0.093 [0.063, 0.118], rf 0.065 [0.042, 0.084], xgb 0.061 [0.043, 0.077] pass [0.06, 0.14] with CI > 0. **lgbm 0.041 [0.024, 0.057] fails the 0.06 floor** (CI > 0 and < 0.14 hold; recorded as a strict xfail). |
| T-05 seed spread (seeds 0/1/2, same size; oracle 0.099/0.091/0.108) | ridge 0.087/0.086/0.089, scr 0.093/0.085/0.100, rf 0.065/0.069/0.080, xgb 0.061/0.044/0.054, lgbm 0.041/0.027/0.053. At 3 × 2,000 training rows (6 anchors × 2,000, oracle 0.081/0.096/0.106): ridge 0.071/0.081/0.097, lgbm 0.028/0.045/0.069, xgb 0.036/0.032/0.079. At Spearman 0.30 the boosters reach 0.79–0.84 of the oracle (lgbm 0.231, xgb 0.246 vs ridge 0.288, oracle 0.294): weak-signal attenuation, not a pipeline defect. |
| T-06 planted leak | `check_available` raises `LeakageError` naming `x_leak` (or drops it); guarded OOS IC < 0.14; the same model with the leak included scores > 0.5 (the test has teeth). The fold driver also raises on any training row with `label_end > T`. |
| T-07 (C3) | test 2022 ⇒ train {2019, 2020, 2021}; a 2021 label ending after T purges 2021 ⇒ {2018, 2019, 2020}. |
| T-08 (C3 analog) | 1Y labels on monthly anchors, test block from 2022-01-31: 2021-01-29 (label_end 2022-01-28) kept, 2021-02-26 purged, 36 train anchors 2018-02-28 … 2021-01-29, 11 purged. Tactical 3M: last train 2019-10-31 without embargo, 2019-07-31 with embargo 3; 12 folds 2014-01 … 2025-06. |
| T-09 (O4) | 9 groups / k = 2: 36 splits, 8 paths, every path covers each anchor once, each group tested 8 times, no train anchor right after a test group, no overlapping label spans. Tactical 12 groups: 66 splits, 11 paths. |
| T-18 | the R1 fit is called once per anchor (7 calls, one anchor each); every model fit (90/10 σ, VIF selection, scr) sees exactly its fold's train anchors and only training labels. |
| T-19 | score parquet sha256 identical across two runs for all 9 learners plus Unified, Composite EW, industry WF, WFO-scr and RF 90/10; RF fit n_jobs 1 vs −1 identical. libsvm's own predict differs by ~5e-15 between identical calls, so `svr` predicts with an explicit-order RBF decision function (matches libsvm to < 1e-10, bit-identical across input alignments). |
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
| `uv run pytest -m "not lake"` | **380 passed, 1 xfailed** (unit 224, leakage 157) | 85 s warm (87 s wall) |
| `uv run pytest -m lake` | **27 passed** (incl. ddl_sandbox DDL + registry) | 9.5 s |
| `uv run python scripts/secret_scan.py --all` | clean | 0.4 s |
| `uv run ideation trials register --family 1Y-june --dry-run` | 50 trials with hashes | ~2 s |
| `uv run ideation doctor` (worktree) | libomp OK; schema_version FAIL "pending migrations [10, 11, 12]", expected until main runs init-db | |

The real ledger was checked before and after the lake suite: versions [1, 2] only.

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
