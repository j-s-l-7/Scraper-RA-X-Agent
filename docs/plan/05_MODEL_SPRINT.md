# 05_MODEL_SPRINT.md: addendum (binding). Ranking mechanism, full model bake-off, consensus, surprise, text and event features, accelerated DAG

Status: binding addendum to `03_RECONCILED_DECISIONS.md`. Where this file conflicts with 01, 02, 03 or CLAUDE.md, **this file wins**, and §0.2 lists every override. Where this file is silent, 03 → 02 → 01 apply as before.
Date: 2026-11-05. Inputs: 00–03, the dossier v2, the past-work extracts (`Models_Results.txt`), branch `phase-0-scaffold` (P0 done: 267 unit/leakage tests and 24 lake tests green), the Gautam/Aravind deep dive, `theme_screen.py`, and the read-only lake probes below.
Slots that depend on `06_PAST_QUANT_WORK_DIGEST.md` are marked **`[[DIGEST:<key>]]`** so they can be found with grep. Implementers use the stated default until the digest fills the slot. Changing a default after the first counted run creates a **new trial**; it never edits the old one.

---

## 0. Probe results and overrides

### 0.1 Probe results (read-only, 2026-11-05)

**Scoring-universe breadth** (NSE bhav EQ/BE/BZ, ≥ 50 of 63 sessions traded, mapped by today's `company.nse_symbol`). These are **upper bounds**. Security stitching (P1) raises the old anchors. The ₹100 cr mcap floor and the fundamentals-at-anchor filter lower every anchor.

| Anchor | traded ≥50/63 | ADV63 ≥ ₹0.5 cr | ≥ ₹0.5 cr & mapped | ≥ ₹0.2 cr & mapped | ≥ ₹1 cr & mapped | all mapped |
|---|---|---|---|---|---|---|
| 2016-06-30 | 1,486 | 887 | 650 | 768 | 535 | 999 |
| 2019-06-28 | 1,630 | 850 | 697 | 833 | 558 | 1,224 |
| 2022-06-30 | 1,888 | 1,339 | 1,171 | 1,333 | 1,022 | 1,564 |
| 2025-06-30 | 2,323 | 1,763 | 1,565 | 1,779 | 1,400 | 2,007 |
| 2026-10-30 | 2,858 | 1,979 | 1,784 | 2,007 | 1,626 | 2,516 |

The owner's target of 1,500–2,200 scored names is reachable **from about 2022**. The 2016–2019 anchors will score roughly 650–850, which is a property of the data, not a bug. Each query took 0.2–0.3 s.

**Earnings events** (`equity.earnings_event`, all from yfinance, 29,424 rows):
- Coverage: 439–750 companies a year from 2013 (2017: 750, 2021: 412, 2025: 640, 2026: 700). 191 rows dated 2027 are scheduled events with no reported EPS.
- Median |surprise| by year is **stable at 13–19%** (2010: 15.5, 2015: 16.4, 2019: 18.1, 2024: 13.6; COVID 2020: 27.9). The beat rate is **41–56%** and exact ties are 0.3–1.7%.
  - If Yahoo had back-filled estimates with hindsight, old years would show near-zero |surprise| and high beat rates. They do not. **The estimates look like as-of-event consensus.**
- The stored `surprise_pct` matches 100·(rep − est)/|est| with a median abs difference of 0.01 but a mean of 8.1, so a minority of rows differ. **Recompute it; never trust the stored column.**
- **EPS levels are on mixed share bases.** RELIANCE quarterly EPS is about 6 in 2017 (today's basis), about 12.8 for Jan–Jul 2024, then 5.85/5.70 for Oct-2024, then 13.39 for Jan-2025. Only **within-row ratios** (reported vs estimate on the same row) are usable.
- Event day: median 36 days after quarter end (P5 18, P95 59).

**Documents** (`equity.document`):
- status='ok': transcripts 22,146 docs / 1,513 cos; PPT 31,592 / 1,794; annual reports 29,335 / 2,558. Annual reports have `doc_date` = NULL; their `fiscal_year` is the report FY.
- **`doc_date` is month-granular.** All 62,383 transcript and PPT rows have day = 1. `period_label` reads like "Oct 2024" or "Q1 FY25 (tijori)".
- `fiscal_year` is the Indian FY in which `doc_date` falls ("May 2026" maps to FY2027), not the period the call discusses. **Do not use it as a period key.**
- Source mix (transcripts): bse 18,284, other 7,196, nse 349, company_site 286, tijori 213, wayback 42, commoncrawl 10.
- doc_date month vs the nearest matched earnings event (same company, window [−75, +105] days):

| Source | Docs | Matched to an event | Event in same month | Event in a later month | Event in an earlier month |
|---|---|---|---|---|---|
| bse | 17,618 | 9,016 | 65% | 8% | 27% |
| other | 3,644 | 2,440 | 76% | 7% | 17% |
| tijori | 213 | 187 | 67% | **30%** | 3% |

  - Tijori docs are labelled by quarter, not by filing month. That justifies a statutory floor for non-BSE sources.
  - The 8% "later" share for BSE is partly the asymmetric match window catching the next quarter's event.
- BSE transcripts start with a Reg-30 cover letter carrying the real filing date. For TCS: "October 14, 2024", digital signature dated 2024.10.14, board meeting October 10, quarter ended September 30, 2024. Q&A markers are present ("Moderator" ×11, "Q&A session", "first question").
- Duplicates (same company, type and month): 11 transcripts, 8 PPTs.
- Transcript size: median 47.5k chars and 18 pages (P5 24.8k, P95 69.2k). The text corpus (`~/Scraper - Screener.in/data/documents`, 2,593 company dirs) is 5.0 GB including annual reports.
- Transcript companies by calendar year: 2016 183, 2017 243, 2018 266, 2019 331, 2020 377, 2021 519, 2022 774, 2023 938, 2024 1,065, 2025 1,223, 2026 1,298.
- **Usable at June anchors** (all lake companies, not universe-restricted): a call available within 200 days / plus a prior call 60–400 days earlier / a PPT within 200 days:

| June anchor | Call within 200 d | + prior call | PPT within 200 d |
|---|---|---|---|
| 2017 | 173 | 140 | 296 |
| 2018 | 186 | 166 | 339 |
| 2019 | 263 | 231 | 496 |
| 2020 | 273 | 244 | 486 |
| 2021 | 366 | 302 | 591 |
| 2022 | 570 | 442 | 777 |
| 2023 | 775 | 707 | 933 |
| 2024 | 907 | 860 | 1,086 |
| 2025 | 1,016 | 967 | 1,211 |

  All of these are survivors (today's companies only).

**Other probes:**
- Delivery %: from 2019-10-01, 1,752 sessions.
- Taxonomy: 12 sectors, 22 industry_groups, 58 industries, **187 sub_industries**.
- Theme sub-industry sizes: Auto Components 101, Heavy Electrical 37, Other Electrical 34, Aerospace & Defense 27, Compressors/Pumps 14, Cables 13, Castings & Forgings 12, Tyres 9, Paints 8, LPG/CNG 8, Lubricants 7, E&P 7, Refineries 7, Airline 3, Power-Transmission 2.

**Wheels:** PyPI ships `xgboost-3.4.1-py3-none-macosx_10_15_x86_64.whl` and `lightgbm-4.7.0-py3-none-macosx_10_15_x86_64.whl` (lightgbm 4.7.0 is already in `uv.lock`). Host: x86_64, macOS 26.3.1, brew `libomp` present at `/usr/local/opt/libomp`. Add `xgboost>=3.4,<3.5`. Both need libomp, and `doctor` must check for it.

**Repo facts that constrain the DAG:**
- `load_methodology` reads only `config/*.yaml`, keyed by file stem (flat; no subdirectories).
- `migrate.py` allows gaps in version numbers but raises `ledger has version X but no file for it`.
- `BAN_SCANNED` = features, labels, universe, models, cv, eval, backtest. `BAN_ALLOWLIST` is empty.
- `IMPORT_RESTRICTED` = features, models, universe.

### 0.2 Overrides of 02 / 03 / CLAUDE.md (binding)

| # | Rule overridden | New rule |
|---|---|---|
| O1 | 03 §C4: universe = top 1,000 by ADV63 | **Two universes per anchor.** `U_score` (training and scoring, §1) and `U1000` (the 03 §C4 universe, kept as a reported slice, so 02 §0.1 coverage acceptance and T-16 still hold as written). The ₹500 cr mcap and ADV20 ≥ ₹2 cr floors apply **only at the idea and portfolio stage**. |
| O2 | 02 §4.2 / 03 §C3: 6 counted 1Y tests (2020–2025) | **Holdout**: Jun-2025 (1Y) and tactical anchors 2025-07-31 … 2026-07-31 (3M; 1M to 2026-09-30) are **sealed** (§8). Counted 1Y tests are **2020–2024 (5)**; 2019 stays indicative. |
| O3 | 02 §5.5 gate 1/2: "≥ 4 of 6" | "IC > 0 in **≥ 4 of 5**", "cost-adjusted top-quintile excess > 0 in **≥ 4 of 5**". Other thresholds are unchanged. |
| O4 | 02 §4.5 CPCV 1Y annual: 10 groups, 45 splits | 1Y annual CPCV on anchors **2016–2024 (9 groups), k = 2, 36 splits, 8 paths**, embargo 1 anchor. T-09 is updated to match. |
| O5 | 02 §1.2: `eps_estimate`/`surprise_pct` live-only | (a) **Outcome use allowed** only in `ideation/outcomes/analyst.py`. (b) **Feature use allowed** only in `ideation/features/events_vendor.py`, as a pre-registered tactical challenger (§6.3). Both modules emit renamed columns (`out_beat`, `out_surp`, `f_vsurp_*`). The literal identifiers appear nowhere else. |
| O6 | CLAUDE.md "never touch `~/Scraper - Screener.in`" | **Read-only** access to `<DOCUMENTS_ROOT>/data/documents/**.jsonl.gz` through `ideation/text/reader.py` only (`open(path, 'rb')`). The setting is `IDEATION_DOCUMENTS_ROOT`, default `~/Scraper - Screener.in`. Writing, listing outside `data/documents`, or importing Scraper code stays forbidden. |
| O7 | 03 §A1 "no reports in git" vs owner's `docs/RANKING_MECHANISM.md` | Commit only the **template** `docs/RANKING_MECHANISM.template.md` (a Jinja2 template with no numbers). The filled file is rendered to `IDEATION_DATA_DIR/reports/<as_of>/RANKING_MECHANISM.md` and copied to `docs/RANKING_MECHANISM.md`, which is **gitignored**. The secret scan blocks it if it is staged. |
| O8 | 01 §4.1/§4.2 bake-off (rf, ridge, lgbm, composite) | The full zoo in §3 (the dossier Phase I and Phase II lists plus OLS), on the same matrix, splits and names. |
| O9 | 03 §E phase table | Replaced by the DAG in §10 (≤ 2 concurrent agents, git worktrees). |
| O10 | 02 §4.4 tactical universe = top 1,000 with delisted names | Tactical uses `U_score_T` (§1.2): delisted and unmapped names included, with the same low floors. |

---

## 1. Universes (P1 owns)

### 1.1 `U_score` for the 1Y model (per anchor A)

A name is in `U_score` if all of the following hold at A:
- It is a stitched security (02 §2.2) in series EQ/BE.
- It traded in ≥ 50 of the last 63 sessions and in at least one of the last 5.
- **ADV63 (zero-filled mean traded value) ≥ ₹0.2 cr.** Primary. ₹0.5 cr is a pre-registered sensitivity.
- `listed_sessions` ≥ 126.
- `company_id` is not null.
- `mcap_pit` (03 §C2 hierarchy) ≥ **₹100 cr**.
- ≥ 60% of core features are non-NaN.

Exclusion reasons extend 002's enum with `adv_below_floor` and `mcap_below_floor_100` (new migration in P1's range).

Expected scored N, read from §0.1 minus about 5–10% for mcap and fundamentals: **2016 ≈ 700, 2019 ≈ 780, 2022 ≈ 1,250, 2025 ≈ 1,650**. P1 prints the measured table into `universe_coverage.extra` and STATUS. Acceptance: the measured value is within ±15% of these, after stitching.

### 1.2 `U_score_T` for tactical models

- Same trading rules and `listed_sessions` ≥ 252.
- `mcap_pit` ≥ ₹100 cr where known. Where mcap is unknown (unmapped names), ADV63 ≥ ₹0.5 cr instead.
- Unmapped and delisted names are included, and their fundamental tilt columns are NaN with an isna flag.

### 1.3 Labels

`xs_rank` and `rank-gauss` targets are computed within `U_score` (1Y) or `U_score_T` (tactical). `excess_univ_med` stays on the **full traded universe** (02 §3.4). `U1000` membership is a boolean column `in_u1000` on `universe_member` (P1 migration). Every metric in §2–§7 is reported on `U_score` (primary) **and** on the `U1000` slice.

---

## 2. The ranking mechanism: raw features to conviction (MS1 owns code; MS3 renders the doc)

This mirrors the dossier pipeline. Each step is a pure function in `ideation/rank/` with its own unit test.

| Step | Function | Definition |
|---|---|---|
| R0 raw | `features.build(view)` | 01 §2.3–2.6 plus §5 (text/events) plus §6 (new price-action). Every column has `available_from ≤ A` (PitView guard). |
| R1 per-cross-section transform | `rank.transform.xs_transform(df, spec)` | (a) winsorize: growth P5/P95 (`winsor`) or median-replace (`dossier_median` trial); others P1/P99. (b) `global` = rank → Φ⁻¹((r − 0.5)/n); `industry` = within-industry_group z with sign flips (groups < 8 names fall back to sector, then the whole cross-section); `hybrid` = §3.3. (c) Missing-applicable values are filled with 0 (the post-transform median) and get `isna_<f>` = 1; not-applicable values (layout) are 0 with `na_layout_<f>` = 1. **One matrix X_A feeds every model** (no native-NaN routing), so "same features" is literally true. |
| R2 model score | `model.predict(X_A)` → `s_i` | Trained on y = rank-gauss of `ret_total` within the universe at each train anchor. |
| R3 rank | `rank.percentile(s)` | `p_i = (rank(s_i) − 0.5)/n` within `U_score` at A; ties averaged. **Ideation Score** `IS_i = round(100·p_i)` (0–100). **Signed score** `S_i = 2p_i − 1 ∈ [−1, 1]` (dossier §5.3 v1.0 view scale). |
| R4 buckets | `rank.buckets(p)` | Deciles D1..D10 (D10 = top), quintiles Q1..Q5, tails **P99** (p ≥ 0.99), **P95** (p ≥ 0.95), P5, P1. Points per decile = 1..10. |
| R5 calibrated expected return | `rank.calibrate.fit(oos_hist)` then `.predict(p)` | **Nested.** For test anchor T, use OOS predictions of counted test anchors **< T only** (and never sealed anchors). Per decile d: `α_d = mean(r − median_univ r)` pooled over those anchors. Shrink `α_d ← 0.5·α_d`. Isotonic (increasing in d; weights = n). Then piecewise-linear interpolation between decile mid-percentiles gives `α(p)`. Live: `exp_ret_mid = π_i + α(p_i)` (01 §4.3). The first counted anchor has no history and prints "n/a". |
| R6 conformal band | `rank.conformal` | Mondrian by decile on `r − median − α(p)` residuals from the same nested set. 80% split-conformal (02 §6.3, time-decay weights, half-life 2 anchors). Realized coverage is reported per test anchor. |
| R7 conviction | `rank.conviction` | 01 §5 rules: High = P95, Medium = top decile, else Low. Notch down for c_i < 0.6, a risk flag, regime misalignment, or the FIN cap; X-Agent consensus notches up (live only). OOD ⇒ "unscorable". |

**Tables every report prints, per model and per horizon (1Y, 3M, 1M):**

- **Table R-A "Score → rank → expected return"**
  - Rows: P99, P95, D10, D9 … D1, P5, P1.
  - Columns:
    - score range at the latest anchor (min–max of `s`);
    - percentile range and Ideation-Score range;
    - mean n per anchor;
    - OOS mean and median excess vs universe median;
    - hit rate (share beating the universe median);
    - beat-Nifty500-PR rate;
    - beat-EW-universe-TR rate;
    - nested shrunk-isotonic α;
    - conformal 80% band;
    - realized band coverage;
    - number of contributing anchors.
  - Two versions: **nested** (decision-grade) and **pooled-all-OOS** (labelled "descriptive, uses later years").
- **Table R-B "IC by anchor"**: rank IC, 90% CI (bootstrap over names), n scored, coverage, top–bottom decile spread, cost-adjusted Q5−Q1 at 12 bps/side × measured turnover.
- **Table R-C "Monotonicity"**:
  - Spearman of bucket index vs bucket mean return per anchor.
  - Count of anchors where P99 > P95 > D10 > D9 (and the mirror at the bottom). This is the dossier §4.2 "high-conviction test".
- **Table R-D "Pooled summary"**:
  - mean IC with 90/95% CI (02 §5.2: bootstrap over anchors for annual, NW + stationary block for monthly);
  - share of positive anchors;
  - IC-IR;
  - DSR (with N);
  - PBO;
  - 90/10 selection score.

**`docs/RANKING_MECHANISM.template.md`** (MS3) is written in dossier style:
1. §1 feature table with sign priors (rendered from the feature YAMLs);
2. §2 transforms with formulas;
3. §3 the model zoo with fixed hyperparameters (from the trial registry);
4. §4 rank, score and buckets;
5. §5 calibration and conformal (formulas);
6. §6 tables R-A..R-D for the **champion** and the composite baseline, per horizon;
7. §7 consensus tables (§4);
8. §8 surprise outcomes (§5);
9. §9 bias block (02 §8.2), mandatory.

Every number is a Jinja variable filled from `ideation.experiment_trial` / `wf_result` / `rank_calibration` for a given `run_id`. A test renders the template with a synthetic run and asserts there are no literal digits outside template variables, except section numbers and fixed hyperparameters, which are read from config.

New tables (MS1 migration range):

```sql
create table if not exists ideation.rank_calibration(model_key text, horizon text, test_anchor date, scheme text check (scheme in ('nested','pooled')),
  bucket text, n int, alpha_raw real, alpha_shrunk real, alpha_iso real, conf_lo real, conf_hi real, coverage real, run_id bigint,
  primary key(model_key, horizon, test_anchor, scheme, bucket));
create table if not exists ideation.oos_prediction(trial_id bigint references ideation.experiment_trial, anchor date, security_id int,
  score real, p real, bucket_dec smallint, label real, ret_total real, excess_univ_med real, fold text,
  primary key(trial_id, anchor, security_id));          -- the single source for consensus, calibration, outcomes
```

`model_key` = `trial.config_hash[:12]`. Large OOS sets also go to parquet in `IDEATION_DATA_DIR/oos/<trial_id>.parquet`. The table is the index.

---

## 3. Model bake-off (E1 builds the zoo; MS1 runs 1Y; MS2 runs tactical)

### 3.1 Rules

- Same matrix X (R1), same splits (`cv/walkforward.py`, 03 §C3/§C8), same names (`U_score`), same target. Every learner sees identical rows. A name with a missing label at an anchor is dropped for **all** learners.
- **Hyperparameters fixed a priori, with no per-window tuning.** Every config is written to `config/trials_1y.yaml` / `config/trials_tactical.yaml` and **inserted into `experiment_trial` (status `registered`) by `ideation trials register` before any fit**. `train` refuses an unregistered config hash.
- Determinism: seeds are fixed; forests predict with `n_jobs=1`; LightGBM `deterministic=True, force_row_wise=True, num_threads=4`; XGBoost `nthread=4, seed=0`.
  - T-19 extends to every learner: same run twice gives an identical score parquet sha256.

### 3.2 Learners and fixed hyperparameters (`ideation/models/zoo.py`, a registry `LEARNERS: dict[str, Callable[[dict], Estimator]]`)

| Key | Implementation | 1Y params | Tactical params |
|---|---|---|---|
| `ols` | `LinearRegression()` on X (with isna flags) | — | — |
| `ridge` | `Ridge(alpha=10)` | α=10 | α=100 |
| `scr` (dossier §3.1 sign-constrained Ridge) | Multiply each feature by its prior sign (+1/−1); drop features with no prior and drop isna flags; `Ridge(alpha=10, positive=True)` | α=10 | α=100 |
| `pridge` | `PolynomialFeatures(2, include_bias=False)` on non-flag columns (squares + interactions), isna flags appended linearly, `Ridge(alpha=100)` | α=100 | α=1000 |
| `rf` | `RandomForestRegressor(n_estimators=500, min_samples_leaf=25, max_features=0.33, random_state=0)` `[[DIGEST:rf_params]]` | as stated | 400 trees, leaf 100 |
| `lgbm` | `LGBMRegressor(num_leaves=15, learning_rate=0.03, n_estimators=400, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, min_data_in_leaf=40)` (fixed rounds, no early stopping) | as stated | min_data_in_leaf 200 |
| `xgb` | `XGBRegressor(max_depth=4, learning_rate=0.03, n_estimators=400, subsample=0.8, colsample_bytree=0.7, min_child_weight=20, tree_method='hist')` | as stated | min_child_weight 100 |
| `mlp` | `MLPRegressor(hidden_layer_sizes=(32,16), alpha=1e-3, learning_rate_init=1e-3, batch_size=256, max_iter=50, early_stopping=False)`, **mean of 5 seeds**. sklearn's `early_stopping` uses a random validation split, which leaks across time, so it is banned (unit test asserts it is False). | 50 epochs | 30 epochs |
| `svr` | `SVR(kernel='rbf', C=1.0, epsilon=0.1, gamma='scale')`; train on a seeded subsample stratified by anchor | ≤ 6,000 rows (all if fewer) | 8,000 rows |

Learner-level ConvergenceWarnings are logged into `trial.config_json.warnings`, not suppressed.

### 3.3 Specifications

- `global`: R1 rank-gauss.
- `industry`: R1 within-industry_group z with sign flips (02 §4.6).
- `hybrid`: concatenation of global and industry columns (2×F features) `[[DIGEST:hybrid_definition]]`.

### 3.4 Phase-I linear family (dossier §3, Models_Results.txt), implemented in `ideation/models/phase1.py`

- **Unified (faithful)**: `S = −z(EV/EBITDA) − z(P/S) + z(FCF yield)`. Here z uses the **training-fold mean/std** of the yield-form columns, with sign handled by the yield inversion: `−z(EV/EBITDA)` is replaced by `+z(ebitda_ev)` and so on. This replicates Models_Results ("training period mean/std").
  - Per-cross-section variant `P1_UNIFIED_XS` is a separate counted trial.
- **Composite (dossier sub-weights, exact)**: `0.3·(−Value) + 0.3·Quality + 0.3·Growth + 0.1·(−Risk)`.
  - Value = 0.4 P/E + 0.3 EV/EBITDA + 0.3 P/S (yield form: +0.4 z(ep) + 0.3 z(ebitda_ev) + 0.3 z(sp)).
  - Quality = 0.4 ROIC + 0.3 ROE + 0.3 ROA.
  - Growth = 0.5 eps_g3 + 0.5 rev_g3.
  - Risk = nd_ebitda.
  - z = per-cross-section rank-gauss. Composite_1Y and Composite_3Y are the **same score** evaluated against 1Y and 3Y forward labels. The 3Y label (anchors ≤ Jun-2023) is **descriptive only**: it needs no fit, has no purge issue, and is not counted. `[[DIGEST:composite_1y_vs_3y]]` confirms this reading.
- **Composite EW sign-prior** (02 §5.5 gate-3 baseline): equal-weight mean of sign-flipped rank-gauss over all core features.
- **Industry equations**: within each industry_group (≥ 8 names), keep the 4 features with the largest |Spearman| to the forward return, subject to sign rules; weights = ρ_k / Σ|ρ_j|.
  - The **in-sample** version (fit and score the same anchor) is printed as a descriptive table only (dossier §3.3), never counted.
  - **`P1_INDUSTRY_WF`**: weights fitted on the train anchors and applied at T. This one is counted.
- **WFO sign-constrained Ridge with selection** (`P1_WFO_3Y`, `P1_WFO_2Y`), inside the train fold:
  1. Univariate pooled OLS of y on each feature with anchor-clustered SEs; keep p < 0.05.
  2. Keep the best |t| member per metric family (the `family:` key in the feature YAML, e.g. {roe, roe_norm, roe_3y}).
  3. Iteratively drop the max-VIF feature until all VIF < 5.
  4. Fit `scr`. If nothing survives, the score is NaN for the fold and the fold is logged as "empty".
  - `[[DIGEST:wfo_selection_details]]`.
- **Final_WFO**: `scr` on {ebitda_ev, sp, fcf_yield}, rolling 3Y (Models_Results §4).
- **PostCOVID**: `scr` on {sp, ebitda_ev, fcf_yield}, train anchors from Jun-2020 onward (expanding). Tests Jun-2023 and Jun-2024 (2 tests, indicative but counted in N). P/FCF and FCF yield are one column (01 §2.3), so the 4-feature dossier spec collapses to 3; this is disclosed.

### 3.5 Pre-registered 1Y trial list (`family = '1Y-june'`, **N = 50** incl. §11 additions #49–50)

| # | Trial id | Content |
|---|---|---|
| 1 | P1_UNIFIED | faithful (train-fold z) |
| 2 | P1_UNIFIED_XS | per-cross-section z |
| 3 | P1_COMP_DOSSIER | dossier sub-weights |
| 4 | P1_COMP_EW | equal-weight sign prior (**the gate-3 baseline**) |
| 5 | P1_INDUSTRY_WF | industry equations fitted on train, applied at T |
| 6 | P1_WFO_3Y | |
| 7 | P1_WFO_2Y | |
| 8 | P1_FINAL_WFO | |
| 9 | P1_POSTCOVID | |
| 10 | OLS_GLOBAL | |
| 11 | SCR_GLOBAL | all features, no selection |
| 12–32 | {RF, PRIDGE, RIDGE, LGBM, XGB, MLP, SVR} × {GLOBAL, INDUSTRY, HYBRID} | 21 trials |
| 33 | RF_GLOBAL_EXT | + dossier "new features": regime broadcast, log mcap, rvol_63, beta_252. RF-lineage step "+regime/mcap/vol" |
| 34–36 | {RF, LGBM, RIDGE}_GLOBAL_MOM | core + 01 §2.5 momentum set |
| 37 | RF_GLOBAL_DMEDIAN | growth cleaning `dossier_median` (03 §C6) |
| 38 | RF_GLOBAL_TRAIN2Y | |
| 39 | RF_GLOBAL_EXPANDING | |
| 40 | RF_GLOBAL_MARCH | March anchor family |
| 41 | P1_COMP_EW_MARCH | |
| 42 | RF_GLOBAL_ADV05 | universe floor ₹0.5 cr |
| 43 | RF_GLOBAL_U1000 | 03 §C4 universe |
| 44–45 | {RF, RIDGE}_GLOBAL_TEXT | covered subset, §5; indicative |
| 46 | CONS_MAJ | consensus rules (§4) |
| 47 | CONS_UNAN | |
| 48 | ENS_RANKAVG | |

The **RF lineage** printed as the dossier chain: RF_GLOBAL (raw) → RF_GLOBAL_EXT (+regime/mcap/vol) → the 90/10 pick (03 §C9). The 90/10 is a **selection rule over trials, not a trial**.

### 3.6 Benchmarks (`family = 'bench-1Y'` / `'bench-3M'`; reported, never selectable, not in N)

- `MOM_12_1`
- `LOWVOL` (−rvol_63)
- `VALUE_ONLY` (EW of ep, sp, ebitda_ev, fcf_yield, bp where applicable)
- `QUALITY_ONLY` (EW of roe_norm, roic, roa, opm, −opm_vol3)
- `GROWTH_ONLY`
- Index and universe levels: Nifty 500 PR (`^CRSLDX`), PIT **cap-weighted** `U_score` TR, PIT **equal-weighted** `U_score` TR, EW full traded universe TR.

### 3.7 Tactical trial list (`family = 'tactical-3M'`, **N = 25** incl. §11 addition #25; 1M is reported from the same fits and never selects)

| # | Trial | Content |
|---|---|---|
| 1 | T_EW_SIGNPRIOR | EW sign-prior composite of the technical set |
| 2 | OLS_T0 | |
| 3 | SCR_T0 | priors in the feature YAML |
| 4–10 | {RF, PRIDGE, RIDGE, LGBM, XGB, MLP, SVR}_T0 | |
| 11–13 | {RF, LGBM, RIDGE}_T0_REGIME | |
| 14–16 | {RF, LGBM, RIDGE}_T1 | + deliv, fpi_sector, event features; train windows start 2020-04 |
| 17–18 | {RF, RIDGE}_T1_TEXT | covered subset, §5 |
| 19–20 | {RF, RIDGE}_T1_VSURP | vendor-surprise challenger, §6.3 |
| 21 | RF_T0_ADV05 | universe floor sensitivity |
| 22 | CONS_MAJ_T | |
| 23 | CONS_UNAN_T | |
| 24 | ENS_RANKAVG_T | |

Expectation, stated up front in every report:
- With 5 annual counted tests and N = 48, **DSR ≥ 0.90 on the 1Y IC series is likely to fail**, even for a real signal. That is an allowed outcome (03 §C9). The composite then ships, and the report says so.
- Tactical has about 138 OOS months and N = 24, so it has real power.

---

## 4. Cross-model consensus (MS3 owns `ideation/eval/consensus.py`)

**Model set M.** The models that vote in the consensus:

| Horizon | M | Members |
|---|---|---|
| 1Y | 10 | GLOBAL spec of the 7 Phase-II learners, plus OLS_GLOBAL, SCR_GLOBAL, P1_COMP_DOSSIER |
| 3M | 10 | the 7 learners _T0, plus OLS_T0, SCR_T0, T_EW_SIGNPRIOR |

Naive baselines (§3.6) are excluded, and the report says why: they are the comparison, not voters.

**Per OOS anchor A** (only from `oos_prediction`, never sealed anchors):
- `votes_i = Σ_m 1[i ∈ D10_m]`.
- Consensus sets:
  - `C_maj = {votes ≥ 6}`;
  - `C_unan = {votes = 10}`;
  - bottom mirror `B_maj = {votes in D1 ≥ 6}`.
  - If |C_unan| < 5, it is marked "too few" for that anchor and excluded from means, with the count of such anchors reported.
- Agreement:
  - mean pairwise Spearman of scores (10×10 matrix, averaged over anchors, printed);
  - **Kendall's W** across the M rankings per anchor, with mean and range;
  - pairwise Jaccard of D10 sets.
- `ENS_RANKAVG`: score = mean over m of `p_{i,m}`. This is a counted trial evaluated like any model.

**Evaluation** (all equal-weighted within the set, per anchor, then over anchors). Compare:
- (a) `C_maj` and `C_unan`;
- (b) **size-matched** top-|C| by each single model, which isolates "agreement" from "concentration";
- (c) `ENS_RANKAVG` top-|C|;
- (d) each model's full D10;
- (e) a random-|C| draw from `U_score` (1,000 draws gives a percentile);
- (f) benchmarks: Nifty 500 PR, PIT CW `U_score` TR, EW `U_score` TR.

Metrics for each:
- mean and median excess vs the universe median, vs N500 PR, vs CW and EW universe;
- hit rate;
- `votes`-IC (Spearman of votes with return);
- spread `C_maj − B_maj`;
- for 1Y: **monthly mark-to-market path** of the June buy-and-hold set (equal-weight at entry, drifting), giving max drawdown, annualized vol and Sharpe vs Rf 7%;
- for 3M: three staggered monthly tranches (Jegadeesh–Titman) produce a monthly return series, giving drawdown, Sharpe and Treynor.

CIs:
- **Stationary block bootstrap** (block 3 months for monthly series; resampling over anchors for annual), 10,000 draws, 90% and 95%.
- **Paired** bootstrap of (consensus − size-matched single model) and (consensus − ENS_RANKAVG), giving P(Δ > 0).

Costs: 12 bps per side × measured name turnover (C12) for the research numbers. Retail cost model for the portfolio rows (§7).

DSR for the consensus rules uses the full family N (they are trials 46–48 / 22–24).

**Answer format** (printed verbatim in the research report): "Consensus (≥6/10) top-decile names beat [benchmark] in x of 5 years; mean excess y% [90% CI a, b]; vs the size-matched best single model: Δ = z% [CI], P(Δ>0) = q." This is filled from data, never pre-written.

---

## 5. "Exceeds expectations" outcomes (PT2 owns `ideation/outcomes/`)

`ideation/outcomes/` is **post-anchor by design**. It reads labels and future facts, so it is **not** PIT-guarded. It is therefore isolated:
- Import contract (extend `test_static_ban.py`): `features`, `models`, `universe`, `rank`, `cv` must not import `ideation.outcomes`.
- `outcomes` writes only to `ideation.outcome` and the report layer.

```sql
create table if not exists ideation.outcome(anchor date, security_id int, horizon text, kind text,   -- 'analyst','fundamental','fundamental_q'
  n_events int, out_beat real, out_surp real, out_fs_sales real, out_fs_pat real, out_accel smallint, extra jsonb, run_id bigint,
  primary key(anchor, security_id, horizon, kind));
```

### 5.1 Analyst surprise as an outcome (`outcomes/analyst.py`, the only module outside §6.3 allowed to name the vendor columns)

- Snapshot dataset `earnings_vendor` (P1 adds it). Columns: company_id, event_date, eps_estimate, eps_reported. It is pulled with a **snapshot-mode** NamedQuery `EARNINGS_VENDOR_FULL`, because the backtest-mode adapter refuses the identifiers. `available_from = event_date + 1`. Filter: `eps_reported is not null` and `event_date ≤ snapshot lake date`.
- Per row: `surp = clip((rep − est) / max(|est|, 0.05·|rep|, 0.01), −2, 2)`, computed from the row's own values (the stored `surprise_pct` is ignored). `beat = 1[rep > est]`; ties count as 0.5.
- Outcome for anchor A and horizon h (1Y: events in (A, A+12M], up to 4; 3M: the first event in (A, A+3M]):
  - `out_beat` = mean beat;
  - `out_surp` = mean surp;
  - `n_events`.
- Report: within the **covered** names (expected 30–45% of `U_score`, mostly larger caps), for each set (P95, D10, C_maj, C_unan, D1, the rest):
  - beat rate and mean surp;
  - difference vs the rest;
  - an anchor-clustered bootstrap CI;
  - the coverage share per anchor;
  - a size-tercile split (coverage is size-biased).

### 5.2 Fundamental surprise without analysts (`outcomes/fundamental.py`)

- 1Y, June anchor Y: FY(Y) is the latest available fiscal year. Outcome = realized FY(Y+1) growth minus the trailing trend:
  - `out_fs_sales = g_sales(FY Y+1) − CAGR3_sales(FY Y−3 → FY Y)`, and likewise `out_fs_pat` for PAT. Use the basis rule (01 F1), require PAT > 0 at both ends of the CAGR, else NaN.
  - `out_accel = 1[out_fs_sales > 0]`.
  - FY(Y+1) results land before A+12M + 60 d, so they are **outcome-only**.
  - Disclosure: these values are as restated in Nov-2026.
- Tactical: `fundamental_q` = next-quarter YoY sales growth minus the trailing-4Q YoY median. Report-only, anchors ≥ 2023-06 (quarterly breadth, 03 §B3).
- Report: the same set comparison as §5.1.
- Additional test: is `out_accel` higher for D10 than D1, using a Cochran–Mantel–Haenszel test stratified by anchor?

---

## 6. New PIT features: events, price action, vendor surprise (P3 and PT2)

### 6.1 Event features (no estimates needed; P3 owns `features/events.py` and the flat file `config/features_events.yaml`)

| Column | Definition | available_from | trainable_from |
|---|---|---|---|
| `ear_3d` | Announcement return: Σ log-returns over sessions [e−1, e+1] of the stock minus its industry_group EW composite, for the latest event with `eps_reported` not null in (A−120 d, A] | close of s(e+1) | 2013-04 (tactical tier T1 uses 2020+) |
| `ear_days` | Sessions since that event (capped at 120) | same | same |
| `pead_drift` | Return from s(e+2) to A minus the group composite, if the event is within 63 sessions | same | same |
| `isna_event` | No event in the window | | |

`event_date` comes from `earnings_event` with only `company_id, event_date, eps_reported is not null`. The allowed snapshot dataset `earnings_dates` carries **no estimate columns**, so it passes the ban list.

### 6.2 Price-action additions (P3 owns, in the flat file `config/features_technical.yaml`)

01 §2.5 covers momentum, reversal, RS, 52-week distances, multi-range breakouts, volume ratio, MAs, vol, beta, idio-vol, liquidity, delivery and RRG. Add:

| Column | Definition | Sign prior | trainable_from |
|---|---|---|---|
| `max_21` | Max daily return over 21 sessions (lottery effect) | − | 2010-02 |
| `skew_126` | Daily-return skew | − | 2010-07 |
| `id_frog` | Information discreteness = sign(r_252) × (%neg days − %pos days) | − | 2011-01 |
| `amihud_63` | Mean(|r|/traded value) | + (illiquidity premium), report-only sign check | 2010-04 |
| `updown_vol_63` | log(Σ value on up days / Σ value on down days) | + | 2010-04 |
| `deliv_lvl_63` | Mean deliv_per over 63 sessions (T1) | + | 2020-01 |

Ownership features use **yearly** changes only (03 §B1).

**Live-only** (ban list, never trained): F&O (2024-07+), bulk/block deals, `fpi_flow_daily`, quarterly shareholding changes before 2026, X-Agent, the expert ledger.

### 6.3 Vendor surprise as a feature (decision: **allowed as a challenger only**)

Reasoning:
- §0.1 shows yfinance estimates behave like as-of-event consensus: stable |surprise| and a ~48% beat rate across 2010–2026.
- A within-row ratio is immune to the share-basis drift.
- Availability `event_date + 1` is exact, given that the event date is the results day.
- Two risks remain unmeasurable: vendor back-fill on rare rows, and coverage selection (today's survivors with analyst coverage).
- So it is never in the core, never in 1Y, and it must beat T1 on the same names to be considered.

Spec:
- Module: `ideation/features/events_vendor.py`, the **single `BAN_ALLOWLIST` entry**. It reads `PitView.earnings_vendor()`, which raises unless `trial.config_json.allow_vendor_estimates is True`.
- Columns: `f_vsurp_last` (surp of the latest event in (A−120 d, A]), `f_vbeat_4q` (beats in the last 4 events / n), `isna_vsurp`.
- **Gate before use** (PT2 acceptance): across 2015–2025, Spearman(surp, ear_3d) > 0 with t > 3 (clustered by quarter). If it fails, trials 19–20 are logged as `failed` (still counted in N) and not run.

---

## 7. Text features from concalls and presentations (PT1 extraction, PT2 PIT features)

### 7.1 Extraction (PT1: `ideation/text/`, no lake writes, no text in the repo)

- **Catalog**: NamedQuery `DOCUMENTS_CATALOG` selects `company_id, doc_type, period_label, doc_date, source, fetch_source, page_count, char_count, pdf_sha256, text_path` from `equity.document` where status='ok' and doc_type in ('concall_transcript','concall_ppt'). Annual reports are **out of scope this sprint** (size, AGM-timing ambiguity); the hook is `[[LATER:annual_report_text]]`.
- **Dedupe** per (company_id, doc_type, doc_date): source priority bse > nse > company_site > other > wayback > commoncrawl > tijori, then larger char_count, then pdf_sha256 order.
- **Reader** (`text/reader.py`): `gzip.open(DOCUMENTS_ROOT / text_path, 'rt')`, one JSON line per page. A unit test greps `ideation/text` for any write-mode `open(` and for path joins outside `data/documents`.
- **Cleaning** (`text/clean.py`):
  - drop page 1 and 2 if they match the cover-letter regex `(Regulation 30|Dear Sir|Scrip Code|Listing Obligations)`;
  - drop safe-harbour paragraphs (`forward[- ]looking statements|safe harbou?r`);
  - drop operator lines (`^\s*Moderator\s*:`);
  - collapse whitespace;
  - lowercase;
  - tokenize with `[a-z][a-z\-']+`.
- **Cover-letter date parse**: the first `(January|…|December) \d{1,2}, 20\d\d` or `Date: 20\d\d\.\d\d\.\d\d` on pages 1–2 gives `filed_date`. Accepted only if it lies in [doc_date − 45 d, doc_date + 75 d].
- **Quarter discussed**: parse `quarter (and .*?)?ended (Month) (\d+), (20\d\d)`, else `period_label` "Qn FYyy", else the last quarter end before doc_date. Stored as `q_end`.
- **Q&A split**: the first match of `(question[- ]and[- ]answer|q\s*&\s*a session|first question|begin the question)`. `prep` = before it, `qa` = after it, `qa_ok` = 1 if found and both parts have ≥ 500 tokens.
- **Lexicon** (`ideation/text/lexicon.py`, committed, versioned `LEX_V1`, about 450 terms, hand-built):
  - Finance-tone categories `pos`, `neg`, `unc`, `lit` are **in the spirit of** Loughran–McDonald. The repo does **not** vendor the LM master dictionary: it is free for academic and personal non-commercial use but licensed, and redistribution in a repo is not clearly allowed.
  - Optional: if the owner places the LM CSV at `IDEATION_DATA_DIR/lexicons/LM_MasterDictionary.csv`, a **diagnostic-only** correlation of LEX_V1 vs LM tone is printed. It is never a trial.
  - India-guidance categories (phrase-level, counted with negation handling: a "not/no/never" within 3 tokens flips the category):
    - `g_up`: order book at record, order inflow strong/robust, guidance raised/upgraded, capacity expansion/addition, utilisation improving/higher, margin expansion, pricing power, demand robust, market share gains, commissioning, debottlenecking.
    - `g_down`: guidance cut/lowered/revised downward, headwinds, demand slowdown/weak demand, pricing pressure, destocking, inventory correction, margin pressure, delays/deferment, input cost inflation, working capital stretch.
    - `capex`: capex, greenfield, brownfield, new plant, investment of rs.
- **Per-document features** (rates per 1,000 tokens):
  - `n_tok`, `tone_net = (pos − neg)/(pos + neg + 1)`;
  - `neg_r`, `unc_r`, `lit_r`;
  - `gup_r`, `gdn_r`, `guide_net = (g_up − g_down)/(g_up + g_down + 1)`, `capex_r`;
  - the same on `prep` and `qa` when `qa_ok`, plus `qa_minus_prep_tone`;
  - optional numeric guidance: `(revenue|sales|volume|ebitda|margin)[^.]{0,40}?(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2}))?\s*%` gives the midpoint `guide_pct_mid`, kept only when a guidance verb (guide|expect|target|aim) appears in the same sentence; isna-heavy.
- **Output**: `IDEATION_DATA_DIR/text/doc_features_<LEX_V1>.parquet`, keyed by (company_id, doc_type, doc_date, pdf_sha256), with `filed_date`, `q_end`, `source`, `qa_ok`, and `available_from` (§7.2). The build is incremental (cache key = pdf_sha256 + lexicon hash) with 4 worker processes.
- Target runtime: ≤ 20 min cold for about 53k docs; ≤ 1 min incremental.

### 7.2 Availability (decided conservatively)

```
available_from = max(
    first_day_of_month(doc_date + 1 month),                          # month-granular doc_date (§0.1)
    filed_date + 1                     if filed_date parsed,
    matched_event_date + 1             if an earnings_event with eps_reported falls in [doc_date-75d, doc_date+45d] for q_end,
    fact_available_from(q_end,'Q',fy_month, event=None)  if source not in ('bse','nse')   # statutory floor for non-exchange sources
)
```

This is never before the quarter's results date. For anchors at month end, a call filed in month m becomes usable at the end of month m+1 at the earliest.

T-24 `test_text_availability` (synthetic) covers:
- BSE doc Oct-2024 with filed 2024-10-14 → 2024-11-01;
- tijori "Q1 FY25" doc_date 2024-07-01 → ≥ 2024-08-15 (statutory);
- an event on 2024-11-05 → 2024-11-06.

T-25 `test_text_future_poison` (lake) applies T-01 to the text features.

### 7.3 Company-level PIT features (PT2: `features/text.py`, flat file `config/features_text.yaml`)

At anchor A:
- **cur** = the latest transcript with `available_from ≤ A` and doc_date within 200 days.
- **prev** = the latest transcript with doc_date in [cur − 400 d, cur − 60 d].

| Column | Definition | Sign | Tier |
|---|---|---|---|
| `d_tone` (**primary**) | tone_net(cur) − tone_net(prev) | + | text |
| `d_guide` (**primary**) | guide_net(cur) − guide_net(prev) | + | text |
| `d_unc` | unc_r(cur) − unc_r(prev) | − | text |
| `tone_lvl`, `guide_lvl`, `gdn_r`, `capex_r` | levels from cur | +, +, −, ? | text |
| `qa_tone`, `qa_minus_prep` | when qa_ok | + | text |
| `ppt_d_guide` | same delta on concall_ppt docs | + | text |
| `ppt_capex_r` | level | ? | text |
| `isna_text`, `isna_text_prev` | flags | | |

Levels are rank-gauss transformed within **covered names only**, and deltas likewise.

Rules:
- **No median fill into uncovered names.**
- The text trials (1Y #44–45, tactical #17–18) are evaluated on the **covered subset**, paired against the same model without text on the **same names**.
- `trainable_from`: tactical 2019-01 (train), test anchors from 2022-01 (about 42 OOS months before the holdout). 1Y is indicative only, with about 4 tests at partial coverage.
- Acceptance prints **universe-restricted** coverage: the share of `U_score` / `U_score_T` with cur, and with cur+prev, per anchor.

### 7.4 LLM summary (live only)

`ideation/ideas/llm_summary.py`, called only by `ideas --llm-summary` for the ≤ 25 shortlisted names. It produces a structured summary of the latest call (guidance, risks, changes vs the prior call).
- The OpenAI key comes from the environment (`OPENAI_API_KEY`). It is never read from another project's `.env` and never logged.
- The module is on `BAN_SCANNED` exclusion by location (`ideas/`). A static test asserts that `ideation/{features,models,cv,eval,rank,backtest}` never import it.
- Output is labelled "live, unvalidated, LLM-generated".

---

## 8. OOS backtest protocol and holdout (E1 builds; MS1/MS2 run)

- **1Y**: June anchors. 03 §C3 timing (entry s1(A); exit last good session ≤ A+12M; `label_available_from = label_end`; purge iff `label_end > T`). Train {T−3, T−2, T−1}.
  - Counted tests: **2020–2024**. Indicative: 2019. Sealed: 2025.
  - March family per 02 §4.2 (counted tests Mar-2021…Mar-2024; Mar-2025 is sealed too).
- **Tactical**: monthly anchors from 2011-01; rolling 36-month train; 12-anchor test blocks; purge per C3; embargo 1M (1M) / 3M (3M).
  - Counted OOS: 2014-01 … **2025-06**. Sealed: 2025-07 … 2026-07 (3M) / 2026-09 (1M).
  - T1 / text tiers: train from their `trainable_from`; counted OOS from 2022-01 (text) / 2023-01 (T1 with 36-month windows from 2020-01).
- **CPCV**:
  - 1Y annual: anchors 2016–2024, 9 groups, k = 2, **36 splits / 8 paths**, embargo 1 anchor. Run for **all 48 trials**: about 5k rows per fit, ~1–20 s per fit, so about 48×36 fits ≈ 30–60 min. PBO comes from these CPCV paths.
  - Tactical CPCV: anchors 2011-01…2025-06, 12 groups, k = 2, 66 splits. Run only for the top 3 trials by WFO plus T_EW_SIGNPRIOR (about 2.5 h, background, checkpointed).
  - Tactical PBO: **CSCV** on the 24 × ~138 monthly OOS-IC matrix with S = 12.
- **Sealed holdout enforcement**: `labels/loader.py: load_labels(anchors, horizon, purpose)`.
  - It raises `SealedAnchorError` for any sealed anchor unless `ideation.holdout_unseal` has a row for this family. That row is written once by `ideation holdout unseal --family … --champion <trial_id> --yes` and records the frozen champion and baseline config hashes.
  - **Every** consumer goes through the loader: fit, nested calibration, conformal, 90/10 stability, consensus thresholds, CPCV, PBO and DSR.
  - T-26 `test_holdout_sealed` monkeypatches every fit and calibrate entry point to record anchors and asserts no sealed anchor appears in the record.
  - After unsealing, the holdout result is reported **once, separately**: champion, composite baseline, consensus and benchmarks. It is never fed back into selection; re-selecting after unsealing requires a new family name.
- **Statistics**: 02 §5.1–5.4 unchanged:
  - NW lags: annual 0, monthly 1Y 11, 3M 2, 1M 0;
  - stationary block bootstrap;
  - within-anchor-and-industry permutation null;
  - DSR on the cost-adjusted Q5−Q1 series and the IC series with the family N;
  - PBO;
  - shuffled-label control (T-04) for every learner.
- **Gates**: 02 §5.5 as amended by O3. Selection follows 03 §C9 (90/10 over trials, `[[DIGEST:ninety_ten_definition]]`; default is 02 §6.6).
- **Mandatory bias block** (02 §8.2), plus three new lines:
  - `Universe: U_score (ADV63≥₹0.2cr, mcap≥₹100cr) N by anchor <list>; U1000 slice reported`
  - `Text/events: survivor-only documents; coverage <list>; vendor estimates (challenger only) yfinance, vintage unverifiable`
  - `Holdout: sealed <anchors>; unsealed <date|no>`

---

## 9. Benchmarks, portfolio comparison and the sector/theme lens (MS3)

### 9.1 What every strategy is compared against

At the IC/bucket level:
- Nifty 500 PR;
- PIT CW `U_score` TR;
- PIT EW `U_score` TR;
- `MOM_12_1`, `LOWVOL`, `VALUE_ONLY`, `QUALITY_ONLY`, `GROWTH_ONLY`;
- P1_COMP_EW (gate baseline);
- P1_COMP_DOSSIER;
- RF_GLOBAL (the dossier RF config);
- the champion;
- CONS_MAJ / ENS_RANKAVG.

### 9.2 Portfolio level

At 1Y June rebalances (monthly MTM), and for tactical with monthly 3-tranche rebalances:
- EW top-25 of each of: champion, P1_COMP_EW, CONS_MAJ, MOM_12_1;
- **CW top-25** (mcap weights, cap 10%) of the same;
- **BL** (Balanced) is added by P7, which appends rows to the same report table.

Costs: **retail model** 03 §D2 (`config/costs.yaml`) on ₹10L notional with integer shares via a simple floor (exact MILP is P7). The research cost of 12 bps/side is shown alongside.

Metrics: CAGR, vol, Sharpe, Sortino, Treynor, IR vs N500 and vs EW universe, max DD, turnover. Block-bootstrap Sharpe/MDD distributions. The dossier thresholds (Sharpe > 2, MDD < 15%) are printed as "aspiration, not gate".

### 9.3 Sector and theme lens (`config/themes.yaml`, MS3; reporting only)

- **By industry_group** (22): IC (groups with ≥ 15 names per anchor), D10 share vs universe share, and D10 excess within the group.
- **Themes**, defined from `theme_screen.py` (sub_industries plus named symbols):
  - `dc_power_ancillaries`: Heavy Electrical, Other Electrical, Cables, Compressors/Pumps, Power-Transmission + NETWEB, BLUESTARCO, VOLTAS, ANANTRAJ, TECHNOE, KEC, KPIL, SCHNEIDER;
  - `aero_via_auto_ancs`: Aerospace & Defense, Castings & Forgings + named symbols;
  - `crude_linked`: Refineries, E&P, LPG/CNG, Gas, Lubricants, Oil equipment/storage + named;
  - `crude_down_beneficiaries`: Paints, Tyres, Airline, CGD names.
- Per theme and anchor:
  - member count in `U_score`;
  - share in D10 and in C_maj;
  - OOS excess of members in D10 vs non-members in D10;
  - the latest-anchor ranked member list (Ideation Score, conviction).
- **Disclosure, printed verbatim**: "Theme membership uses Nov-2026 labels and themes chosen in Nov-2026 with hindsight; historical theme results are biased and are never used for selection or weighting. Expert posts (X) are live, unvalidated context."

---

## 10. Phase DAG: fastest safe order, ≤ 2 concurrent agents

### 10.1 Parallelism rules (bind every phase card)

1. **Worktrees**: `git worktree add ../wt-<phase> -b phase-<id>-<slug> main`. Each concurrent agent works only in its own worktree. The orchestrator merges to `main` in DAG order, rebasing the second branch if needed.
2. **Migrations**:
   - Number ranges: P1 003–009, E1 010–014, P2 015–019, PT 020–024, P3 025–029, MS 030–039, P6 040–044, P7 045–049, P8 050–054, P9 055–059.
   - **Only `main` applies migrations to the real `ideation` schema.** Never run `ideation init-db` from a worktree, because another worktree would then fail with "ledger has version X but no file".
   - A branch tests its DDL with the new lake fixture `tests/lake/conftest.py::ddl_sandbox`: `BEGIN; apply pending files; yield; ROLLBACK` (Postgres DDL is transactional).
   - After merge, the orchestrator runs `uv run ideation init-db` on `main`.
3. **Config**: flat files (the loader is flat):

| File | Owner |
|---|---|
| `config/default.yaml` | P1 (universe keys only; others append under their own top-level key via a reviewed merge) |
| `config/features_fundamental.yaml` | P2 |
| `config/features_technical.yaml`, `config/features_events.yaml` | P3 |
| `config/features_text.yaml` | PT2 |
| `config/trials_1y.yaml` | MS1 |
| `config/trials_tactical.yaml` | MS2 |
| `config/themes.yaml` | MS3 |
| `config/costs.yaml`, `config/profiles.yaml` | P7 |

   `features/registry.py` (P2) loads every `features_*.yaml`. The single feature map of 01 §2.7 becomes this file set.
4. **CLI**: each module exposes its own Typer sub-app (`ideation/<pkg>/cli.py: app`), registered in `ideation/cli.py` by a one-line `add_typer`.
   - Owners: `snapshot`/`universe`/`labels` P1; `features` P2 (P3 and PT2 add sub-commands under their own sub-apps `features-tech`, `text`); `trials`/`train`/`backtest` E1/MS; `holdout` E1; `ideas` P6; `portfolio`/`pick` P7; `chat` P8; `serve`/`daily` P9.
   - Conflicts in `cli.py` are trivial one-line merges.
5. **Rate limits**: at most 2 agents at once. Long runs (CPCV, tactical bake-off) go in the background with per-(trial, fold) parquet checkpoints, so an interrupted agent resumes.

### 10.2 Order (binding; owner-approved "results first"; replaces the W0–W6 wave table)

**Rule: nothing after the research report starts until MS3 has merged** (P6, P7, the 04 expert-signal module, P8, P9).

| Step | Lane A (1Y track) | Lane B (eval + text + tactical) | Start condition | Est. agent wall-clock |
|---|---|---|---|---|
| S0 | **P1** snapshot/universes/labels (running) | **E1** eval stack + model zoo + rank/* + trials registry, synthetic only | now | P1 ≈ 3 h remaining; E1 4–5 h |
| S1 | **P2** fundamentals (`features_fundamental.yaml`) | **PT1** text extraction (P0 adapter + `DOCUMENTS_CATALOG`) | A: P1 merged. B: E1 merged | 3–4 h each |
| S2 | **P3** technical / ownership / regime / RRG / events (§6.1–6.2) | **PT2** text PIT features (§7.2–7.3) + `events_vendor.py` validity gate | A: P2 merged. B: PT1 + P1 merged | P3 3–4 h; PT2 2–3 h |
| S3 | **MS1-core**: 1Y trials except #44–45 + CPCV | **MS2** tactical bake-off (checkpointed background compute) | A: P2 + P3 + E1 merged. B: P3 + PT2 merged | MS1-core 3 h + ≤ 1.5 h compute; MS2 3 h + 3–5 h compute |
| S4 | **MS1-text**: trials 44–45 on the covered subset (same family, N unchanged) **+ outcomes module (§5, moved from PT2)** | MS2 continues | A: PT2 merged | 1.5–2 h |
| S5 | **MS3** research report: consensus, benchmarks, theme lens, `RANKING_MECHANISM`, then holdout unseal + separate holdout report | (idle / MS2 finishing) | MS1 + MS2 merged | 3–4 h |
| — | **Owner receives the research report** | | | planner estimate ≈ 20–24 h after P1 merges (conservative ≈ 28 h) |
| S6 | **P6** ideas engine | **04 expert-signal module** (migrations 060–064, owns `config/experts.yaml`) | MS3 merged | 3 h; 2–3 h |
| S7 | **P7** BL + capital tiers + single-stock pick | **P8** chat (P7 tools stubbed until P7 merges) | P6 merged | 4–5 h; 4 h |
| S8 | **P9** API / daily / launchd | — | P7 + P8 merged | 3 h |

10.2.a Critical path: P1 → P2 → P3 → MS2 (+compute) → MS3, with E1 → PT1 → PT2 in parallel (must finish before MS2). If MS2 compute overruns, MS3 starts on the 1Y family and adds tactical sections when MS2 merges; the holdout is unsealed **once, after both champions are frozen**. Owner-visible interim outputs: after S1 coverage tables; after S3 interim R-A…R-D (working output only).
10.2.c Phase-card changes: outcomes (`ideation/outcomes/{analyst,fundamental,cli}.py`, import contract, outcome-row acceptance) move from PT2 to MS1-text; outcomes DDL stays in E1's 010–014; the P9 "expert ledger" bullet in §10.3 is superseded by the 04 module at S6.
10.2.d Concurrency: ≤ 2 agents; each in its own git worktree from up-to-date `main`; only `main` runs `init-db` (branches use `ddl_sandbox`); in S3 run MS2 compute with `nice -n 10`, LightGBM/XGBoost `num_threads=4`, forests `n_jobs=4`; merge order inside a step: Lane A first, Lane B rebases.

### 10.3 Phase cards

**P1: snapshot, universes, labels** (03 §E P1 content, plus O1/O10)
- Adds:
  - `U_score`, `U_score_T`, `in_u1000`;
  - snapshot datasets `earnings_dates` (no estimates) and `earnings_vendor` (snapshot-mode query);
  - `labels/loader.py` with sealing (§8) and the `holdout_unseal` table (migration 003–009).
- Files: `ideation/lake/{snapshot,symbols,prices}.py`, `ideation/universe/*`, `ideation/labels/*`, `ideation/pit/contract.py` (real PitView, plus the `earnings_vendor()` gate), `sql/ideation/003_*.sql…`.
- Acceptance:
  - 02 P1 row tests (T-01 prices, T-03, T-15, T-16, T-20…T-23) green;
  - coverage ±2 pp on U1000;
  - `U_score` N per anchor within ±15% of §1.1, printed;
  - T-26 skeleton;
  - `uv run ideation snapshot --full` ≤ 8 min;
  - `uv run ideation universe build --anchors june,monthly` ≤ 5 min.
- Owner sees: the coverage table (U1000 vs U_score per anchor), exit counts, label cross-check.

**E1: evaluation stack and zoo** (no lake; runs alongside P1)
- Files:
  - `ideation/cv/{walkforward,cpcv,cscv}.py`;
  - `ideation/eval/{metrics,inference,dsr,pbo,bootstrap,selection,consensus_math}.py`;
  - `ideation/models/{zoo,phase1,select_vif}.py`;
  - `ideation/rank/{transform,percentile,buckets,calibrate,conformal,conviction}.py`;
  - `ideation/trials/{registry,cli}.py`;
  - migration 010–014 (`oos_prediction`, `rank_calibration`, `outcome`);
  - add `xgboost` to pyproject + `uv.lock`; `doctor` checks libomp.
- Acceptance (synthetic):
  - T-04 (shuffled; every learner);
  - T-05 (planted ρ = 0.10 recovered by rf/ridge/lgbm/xgb/scr within [0.06, 0.14]);
  - T-06, T-07 (amended: test 2022 → train {2019, 2020, 2021}), T-08, T-09 (O4: 36 splits), T-18, T-19 for all learners;
  - `scr` coefficients all ≥ 0 after the flip;
  - VIF selector drops a planted collinear pair;
  - isotonic calibration monotone;
  - conformal coverage on synthetic iid within [0.75, 0.85];
  - DSR/PBO match published worked examples (Bailey–López de Prado) to 1e-3;
  - MLP `early_stopping` banned;
  - `pytest -m "not lake"` < 3 min.
- Owner sees: nothing on the market yet. The STATUS entry lists the zoo and its fixed parameters.

**P2: fundamental features**
- Content: 03 §E P2, with `config/features_fundamental.yaml` (adds the `family:` key for VIF selection and a `prior:` key).
- Acceptance:
  - 01 P2 (golden values for 5 names);
  - T-01 on fundamentals;
  - coverage per anchor on `U_score`;
  - features for all June+March anchors ≤ 3 min.
- Owner sees: the feature coverage table per anchor.

**PT1: text extraction** (alongside P2; depends only on P0 plus the catalog query)
- Files: `ideation/text/{reader,clean,lexicon,extract,cli}.py`; `ideation/lake/queries.py` (+ `DOCUMENTS_CATALOG`; append-only edit); `IDEATION_DOCUMENTS_ROOT` setting.
- Acceptance:
  - unit tests on 6 synthetic transcripts (cover letter stripped, safe harbour stripped, Q&A split, negation, numeric guidance);
  - the read-only static test;
  - real run `uv run ideation text extract --types concall_transcript,concall_ppt` ≤ 20 min cold, ≤ 1 min warm;
  - doc counts after dedupe = 22,135 ± 50 transcripts and 31,584 ± 50 PPTs;
  - `filed_date` parse rate on BSE transcripts ≥ 80%;
  - `qa_ok` rate printed;
  - TCS Oct-2024 `filed_date` = 2024-10-14.
- Owner sees: per-year coverage and a tone-distribution sanity table (2020-Q1 calls should show a negative mean `d_tone`).

**P3: technical, ownership, regime, RRG, events**
- Content: 03 §E P3 plus §6.1/§6.2; files `features/{technical,flows,events}.py`, `regime/*`.
- Acceptance:
  - 01 P3;
  - T-01 on technical and event features;
  - `ear_3d` uses no data after s(e+1) (poison test);
  - tactical matrix for all monthly anchors on `U_score_T` ≤ 8 min.
- Owner sees: regime history and the RRG table at latest.

**PT2: text PIT features + outcomes** (alongside P3)
- Files: `features/text.py`, `features/events_vendor.py` (the single allow-list entry), `outcomes/{analyst,fundamental,cli}.py`; static-test updates (BAN_ALLOWLIST = {`ideation/features/events_vendor.py`}; import contract on outcomes).
- Acceptance:
  - T-24, T-25;
  - the vendor validity gate (§6.3) computed and printed;
  - outcome rows for all June anchors 2016–2024 and monthly anchors 2014–2025-06;
  - universe-restricted text coverage per anchor printed;
  - runtime ≤ 10 min.
- Owner sees: the text coverage table and "does surprise sign agree with the announcement return" (t-stat).

**MS1: 1Y bake-off**
- Commands: `uv run ideation trials register --family 1Y-june` (48 rows), then `uv run ideation train --horizon 1y --bakeoff --cpcv`.
- Acceptance:
  - all 48 trials have status ok/failed;
  - `wf_result` and `oos_prediction` for 2019 (indicative) and 2020–2024;
  - CPCV 36 splits;
  - DSR, PBO and 90/10 printed;
  - gates evaluated honestly;
  - T-02 future-poison scores on Jun-2021;
  - T-26 green;
  - runtime ≤ 90 min (WFO ≤ 20 min plus CPCV ≤ 60 min).
- Owner sees: tables R-A…R-D for every trial, the RF-lineage chain, the champion or "composite ships".

**MS2: tactical bake-off**
- Same structure (24 trials, 3M primary, 1M reported). Text and VSURP trials run on the covered subset with paired comparisons.
- Runtime ≤ 3 h background (checkpointed); CPCV for the top 3 + baseline ≤ 2.5 h.
- Acceptance as MS1, plus tactical gates (02 §5.5).

**MS3: research report** (the owner's questions)
- Files: `ideation/eval/consensus.py`, `ideation/report/research.py`, `docs/RANKING_MECHANISM.template.md`, `config/themes.yaml`.
- Commands:
  - `uv run ideation report research --family 1Y-june --family tactical-3M` → `IDEATION_DATA_DIR/reports/<date>/RESEARCH_OOS.md` + `RANKING_MECHANISM.md` (copied to the gitignored `docs/`);
  - then `uv run ideation holdout unseal …` (once, after the champion is frozen) and `report research --holdout`.
- The report answers, with CIs:
  - (1) the ranking mechanism and score → rank → expected return per model and horizon;
  - (2) which models beat P1_COMP_EW / the RF / naive baselines;
  - (3) consensus vs single models vs size-matched vs rank-average vs benchmarks;
  - (4) beat rates and fundamental surprise of top / consensus names;
  - (5) whether text and event features add value on covered names;
  - (6) sector/theme lens;
  - (7) the holdout result, separately;
  - (8) the bias block.
- It must state plainly any negative result, for example "RF did not beat the composite (ΔIC = …, P(Δ>0) = …)".
- Acceptance: the report renders only with the bias block, N, CIs and sealed/unsealed status (test); the template has no literal numbers (test); runtime ≤ 15 min.

**P6 / P7 / P8 / P9**: as 03 §E, with these changes:
- P6 uses `rank/*` (nested calibration fitted on all unsealed counted OOS for live) and shows Ideation Score + conviction.
- P7 appends BL rows to the §9.2 table.
- P8 tools add `ranking_table(model, horizon)`, `consensus(as_of)`, `outcomes(symbol)`.
- P9 adds the **expert ledger**:
  - `ideation.expert_call(call_id, author, posted_at, text_hash, thesis, symbols[], direction, horizon)`, live-only, on the ban list;
  - forward returns are tracked from `posted_at + 1 session`;
  - never in a backtest;
  - reported as "forward record since 2026-11".

### 10.4 Every phase card also includes

- `uv run ruff check .`, `uv run pytest -m "not lake"`, `uv run pytest -m lake` (DDL via sandbox), `uv run python scripts/secret_scan.py --all`;
- a STATUS entry with commands, runtimes and measured numbers;
- "never run init-db from a worktree";
- no data, reports or `docs/RANKING_MECHANISM.md` committed (add to `.gitignore` in E1).

---


---

## 11. Digest reconciliation (binding; fills every `[[DIGEST:*]]` slot from `06_PAST_QUANT_WORK_DIGEST.md`)

| Slot | What the owner's sources say (06) | Binding decision |
|---|---|---|
| `rf_params` | Hyperparameters for every learner are **not specified** (06 §4.1). | Keep §3.2 values; documented as a-priori choices, not source facts. |
| `hybrid_definition` | "Hybrid" is **undefined**; the notebook itself asks to specify it (06 §4.2, §9.7). | Keep §3.3 (global ⊕ industry columns). RANKING_MECHANISM states it is our definition. The legacy "RF-hybrid 0.21 vs 0.05" is not reproduced. |
| `composite_1y_vs_3y` | Composite_1Y and Composite_3Y are the same legacy score evaluated vs fwd_1 and fwd_3; sub-weights V = .4 P/E + .3 EV/EBITDA + .3 P/S, Q = .4 ROIC + .3 ROE + .3 ROA, G = .5 EPS_g3 + .5 Rev_g3, R = ND/EBITDA (06 §10 item 8). | Confirms §3.4. |
| `wfo_selection_details` | Univariate p < 0.05, VIF < 5, best variant per family; Ridge with bound (sign) constraints; no intercept; γ via inner CV (source silent on γ); also 2Y/5Y windows and a no-COVID variant (06 §10 item 9). | Keep §3.4 with **fixed γ** (α = 10; no per-window tuning rule wins). 5Y window is infeasible (fundamentals broad only from FY2015) — disclosed. The no-COVID view is reported as a **slice** of P1_WFO_3Y (excluding tests 2021, 2022), not a new trial. |
| `ninety_ten_definition` | Sources say only "90 expected return / 10 stability = variance (b/w time) of features"; neither term is defined; the notebook flags "90/10 inconsistency" (06 §4.5, §9.2). Two readings: (a) per-security score blend, (b) model-selection criterion. | Implement **both** (06 §10 item 14). (b) = selection rule over trials (02 §6.6, as before). (a) = new trials below: final score = 0.9·rank(pred) + 0.1·rank(−σ_t), where σ_t = mean over core features of the security's std of its cross-sectional rank-gauss value across the train-window anchors (needs ≥ 2 anchors; else rank 0.5). Every "with 90/10" result is reported **against the identical config without 90/10** on identical names/years (fixes the notebook's unequal-comparison problem). |

**Trial-list additions (registered before any fit; N updated):**
- 1Y family `1Y-june`: **#49 RF_GLOBAL_9010SEC** (reading a on RF_GLOBAL), **#50 RF_GLOBAL_EXT_9010SEC** (reading a on RF_GLOBAL_EXT). With #12 RF_GLOBAL and #33 RF_GLOBAL_EXT this is the digest's 2×2 feature-impact ablation {core, core+regime/mcap/vol} × {no 90/10, 90/10} (06 §10 item 15). **N = 50.**
- Tactical family `tactical-3M`: **#25 RF_T0_9010SEC**. **N = 25.**
- The RF lineage chain in reports: RF_GLOBAL → RF_GLOBAL_EXT → {RF_GLOBAL_9010SEC, RF_GLOBAL_EXT_9010SEC} → 90/10(b) pick; the dossier reference values (0.223 / 0.233 / 0.25, different dataset) are printed beside ours as "reference, not target".

**Additional report items (MS1 computes, MS3 renders) from 06 §10:**
1. Univariate IC table (item 5): per feature × June anchor, Spearman vs 1Y forward return (and vs 2Y where labels exist, descriptive), N, within-top/bottom-quintile Spearman; averaged and ranked as in the source (RES p.33/46).
2. Feature definitions follow 06 §2.1 (the 23 WFO candidates) with aliases for the industry-study names (06 §2.2); FCF yield gets prior **+** (06 item 4); ROE_5Y/ROIC_5Y computed and reported, excluded from the core set (01 §2.3).
3. Growth cleaning per 06 item 3 (NaN if prior-year base NaN; flag identical extreme growth > 500% in consecutive years); per-year P5/P95 → median is the `dossier_median` trial.
4. Quantile diagnostics with **one denominator** (number of counted test anchors) for every count (06 item 16, §9.6); explicit definitions printed: hit rate = share beating the universe median; beat-N500 = share beating Nifty 500 PR.
5. Market-cap-conditioned transition matrix (06 item 17): within each PIT cap tercile, predicted D10/Q5 → realized quintile counts across anchors; modal/mean/median realized quintile.
6. Breadth/divergence check per anchor (06 item 18): share of names with negative 1Y return, their mean, universe median vs Nifty 500.
7. **Reconciliation log** (06 item 30): every reference number printed beside ours records dataset, N, years and denominator.
8. Portfolio layer (P7) adds Ω factor c ∈ {1 (spec), 3 (He–Litterman, notebook)} as a config switch, both reported; RF-penalty flag (threshold 18%) default **off**, tested both ways (06 items 20, 23).

---

## 12. Owner addenda, 2026-11-11 (binding; extends §7 and §10)

1. **Model name.** The quant model is called **Resurrection**.
2. **PT3: annual-report text features** (new phase, starts after MS3 merges; fills the `[[LATER:annual_report_text]]` hook in §7.1).
   - Corpus: 29,335 annual reports / 2,558 companies; `doc_date` is NULL, only `fiscal_year` is known.
   - Features aimed mainly at the 1Y model: auditor remarks (qualifications, emphasis of matter), related-party transactions, contingent liabilities, tone of the management discussion.
   - Availability: cover-page filing date if parseable, else a fixed lag after FY end; take the later of the two until real filing dates are checked.
   - Evaluated as a challenger trial: same model with and without the features, same covered names, paired comparison. Enters Resurrection only if it wins.
   - Multi-call text trends (e.g. slope of tone/guidance over the last 4 calls) are built here as an extension of §7.3 deltas.
   - At most one text-embedding challenger, and only if the research report shows text helps at all. No sequence models or knowledge graph: ~22k calls and ~6 test years are too few to train them without overfitting.
   - The pre-registered trial lists (N = 50 for 1Y, 25 for tactical) stay fixed. Any added trial is registered before running and counts toward DSR N.
3. **Compute policy.** Where it improves estimation rather than widening the search: seed-averaged ensembles, full 36-split CPCV for every trial, bootstrap >= 10,000 resamples. When the Mac is otherwise idle the §10.2.d thread/`nice` caps may be lifted for MS1/MS2 compute.
4. **Implementers.** Phase implementers are Sonnet 5.5 agents (was Opus). The leakage and future-poison tests are the quality gate before any merge to `main`.
