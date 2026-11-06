# Scraper-RA-X-Agent — Investment Ideation System

Private, personal-use system that ranks **Indian equities** by expected future return and turns the ranking into
**stock ideas** (1-year and tactical 1–3-month horizons) and **Black-Litterman model portfolios** (including small-capital
tiers of ₹10L / ₹1L / ₹10k and a single-stock pick for ₹1k / ₹5k / ₹10k).

> **Current state (2026-11-06): the data foundation, the feature pipeline for fundamentals, the text extractor and the
> evaluation stack are built and merged. No model has been trained yet, so the system does not output stock
> recommendations today.** The first thing it will produce is a research report (model results, ranking tables,
> consensus across models); the ideas engine and portfolios come after that. See [Status](#2-status-and-roadmap).

> Personal research tool, not investment advice. Model rankings are probabilistic. Backtests carry residual biases
> (restated financials, current sector labels, partial survivorship) that every report must disclose in a mandatory
> "Data & Bias" block.

---

## Contents

1. [What it is built from](#1-what-it-is-built-from)
2. [Status and roadmap](#2-status-and-roadmap)
3. [How the system works, end to end](#3-how-the-system-works-end-to-end)
4. [Point-in-time discipline (the anti-leakage rules)](#4-point-in-time-discipline-the-anti-leakage-rules)
5. [Universes: which stocks get scored](#5-universes-which-stocks-get-scored)
6. [Labels: what the model predicts](#6-labels-what-the-model-predicts)
7. [Features](#7-features)
8. [Models: the pre-registered bake-off](#8-models-the-pre-registered-bake-off)
9. [From score to recommendation: the ranking mechanism](#9-from-score-to-recommendation-the-ranking-mechanism)
10. [Validation protocol and the sealed holdout](#10-validation-protocol-and-the-sealed-holdout)
11. [Portfolios, capital tiers and the single-stock pick (planned)](#11-portfolios-capital-tiers-and-the-single-stock-pick-planned)
12. [Setup](#12-setup)
13. [Command reference](#13-command-reference)
14. [Repository layout](#14-repository-layout)
15. [Database objects](#15-database-objects)
16. [Configuration](#16-configuration)
17. [Testing and quality gates](#17-testing-and-quality-gates)
18. [Safety rules](#18-safety-rules)
19. [Working on the project (phases, worktrees, agents)](#19-working-on-the-project-phases-worktrees-agents)
20. [Known limitations and residual biases](#20-known-limitations-and-residual-biases)
21. [Documentation map](#21-documentation-map)

---

## 1. What it is built from

| Component | Role |
|---|---|
| **Scraper** (`j-s-l-7/Scraper`, local checkout `~/Scraper - Screener.in`) | The local equity lake: PostgreSQL 15 at `localhost:5433/equity_lake`, schemas `equity` and `macro`. **Read-only** to this system. Daily NSE bhav copies, Screener fundamentals, shareholding, corporate actions, earnings events, concall transcripts and presentations, macro series. |
| **Quant Equity System Dossier v2** (`Quant_Equity_System_Dossier_v2.md`, outside this repo) | The methodology: Phase I linear factor models, Phase II non-linear ensembles (random forest etc.), Phase III Bayesian (Black-Litterman) portfolio construction, Phase IV live management, backtesting and "platinum standard" upgrades. |
| **Owner's original quant work** (7 PDFs and a 27-page handwritten notebook) | Source of the dossier. Digested in plan document 06 (the past-quant-work digest) under `docs/plan/`; the Phase I specs and the 90/10 reading come from here. |
| **Rebirth Assistant** (`j-s-l-7/rebirth-assistant`) | The chat-copilot pattern for the planned chat phase: the LLM never produces a number, tools supply every number. |
| **X-Agent** (`j-s-l-7/x-agent`) | Live sentiment overlay. Never trained on, never backtested. A planned expert-signal module (Gautam Baid's X calls as a hard filter/boost; Aravind as regime narrative tags only) lives in `docs/plan/04_EXPERT_SIGNALS.md`. |

Outputs go to a new **`ideation` schema** in the same Postgres database, written by a dedicated role `ideation_rw`
(`SELECT`-only on `equity`/`macro`, `BYPASSRLS` because 57 lake tables have row-level security with no policies).
Large artifacts (snapshots, feature tables, label parquets, logs) live in `IDEATION_DATA_DIR` (default
`~/Ideation-data`), **never in git**.

---

## 2. Status and roadmap

### 2.1 What is built (merged to `main`)

| Phase | What it delivers | Headline measured result |
|---|---|---|
| **P0 scaffold** | uv/Python 3.12 project, Typer CLI, config + run registry, read-only lake adapter with a backtest ban list, write guard, numbered migrations, PIT lag constants, secret scan + pre-push hook, `doctor` | 292 tests; role and read-only enforcement proven against the live lake |
| **P1 snapshot, universes, labels** | One-transaction parquet snapshot; trading calendar; security stitching across renames/ISIN changes; PIT market cap; two PIT universes; back-adjusted prices; forward-return labels with exit handling; `PitStore`/`PitView` leakage guard; sealed-holdout label loader; Data & Bias block | Full snapshot 195 s; universes 199 anchors in ~5 min; labels 971,740 rows in ~3 min; exit counts within 0.6% of the earlier probe; top-500/1000 coverage within 0.3 pp |
| **E1 evaluation stack and model zoo** (synthetic data only) | CV splitters (annual/monthly walk-forward, CPCV, CSCV); metrics, inference, DSR, PBO, bootstrap, permutation null; every learner in the zoo with fixed hyperparameters; Phase-I linear family; ranking-mechanism functions (R1–R7); trials registry that refuses unregistered configs | 388 tests; DSR reproduces the published worked example (0.9004); bit-identical scores across runs for all 14 scorers |
| **P2 fundamental features** | 32 fundamental feature columns, a feature registry, the feature-completeness filter wired into the universe | 33 anchors × ~1,400 names built in ~86 s; hand-derived golden values for 5 companies match to 1e-9; bit-identical features under future-data poisoning |
| **PT1 text extraction** | Rule-based extraction from 22,335 concall transcripts and 31,614 presentations: tone, uncertainty, guidance language, numeric guidance, Q&A split, filing dates | Cold run 272 s, warm 12.8 s; filing date parsed for 99.5% of BSE transcripts |

Latest test counts on `main`: **510 offline tests** and **86 lake tests** (against the live database), ruff clean, secret
scan clean. Per-phase commands, runtimes, deviations and risks are in [`docs/STATUS.md`](docs/STATUS.md).

### 2.2 What is next (binding order, "results first")

```
now  →  P3 technical/ownership/regime/RRG/event features
        PT2 text point-in-time features (needs PT1 + P1)
     →  MS1 1Y model bake-off (50 pre-registered trials, CPCV)
        MS2 tactical bake-off (25 pre-registered trials)
     →  MS3 research report (consensus across models, benchmarks, theme lens, RANKING_MECHANISM tables)
     →  ONE-TIME holdout unseal after the champions are frozen, reported separately
     →  P6 ideas engine → P7 Black-Litterman + capital tiers + single-stock pick
        → expert-signal module, P8 chat → P9 API / daily scheduling
```

Nothing after the research report starts until the report is merged. **Stock recommendations first appear at P6.**
A model that fails the promotion gates is an allowed outcome: the equal-weight composite baseline ships and the report
says so.

### 2.3 Open owner decision

The fixed LightGBM and XGBoost hyperparameters (plan 05 §3.2) attenuate a weak planted signal on synthetic data
(LightGBM recovers 0.041 of a planted Spearman 0.10, below the 0.06 test floor; XGBoost 0.061 only at the registered
seed). Ridge, sign-constrained ridge and random forest pass. Options: keep them as pre-registered and report the
handicap, or make one documented, synthetic-data-only change before the trial list is frozen.

---

## 3. How the system works, end to end

```
 local lake (equity, macro; read-only)
        │  ideation snapshot --full          one REPEATABLE READ READ ONLY transaction
        ▼
 parquet snapshot  ──────────────►  manifest: sha256 per file, lake max dates, git sha, config hash
        │   every dated row carries `available_from` (when the data was really knowable)
        ▼
 calendar → security stitching → PIT market cap → PIT universes → back-adjusted prices → labels
        │
        ▼
 PitStore / PitView  (the only door features may use; asserts max(available_from) ≤ anchor)
        │
        ├── fundamental features (P2)      ├── text features (PT1 → PT2)
        ├── technical/regime/RRG (P3)      └── event features (P3)
        ▼
 R1 per-cross-section transform (rank → normal score)
        ▼
 models (pre-registered trials) → score s_i per stock per anchor
        ▼
 R3 rank → Ideation Score 0–100, deciles/quintiles/tails
 R5 calibrated expected return, R6 80% conformal band, R7 conviction
        ▼
 research report → (later) ideas, Black-Litterman portfolios, chat, API
```

**Anchors.** The model is evaluated at fixed dates (anchors): every June (and March) for the 1-year model, every
month-end for the tactical model. At an anchor A, features use only data with `available_from ≤ A`; the label is the
return from the first session after A to A + horizon.

---

## 4. Point-in-time discipline (the anti-leakage rules)

Look-ahead leakage is the main way a backtest lies, so the system is built around preventing it and proving it.

- **Availability lags** (`ideation/pit/lags.py`, constants pinned by tests, not config). Company financial facts become
  available at the SEBI filing deadline + 1 day (later if an actual earnings event date is later). Shareholding at its
  filing deadline; macro series at their release date; labels at `label_end`. COVID-era filing extensions are verified
  against SEBI circulars and only ever move dates **later**.
- **`PitStore` / `PitView`** (`ideation/pit/contract.py`). Feature builders receive an immutable, pre-filtered view and
  never a database connection. Every accessor asserts `max(available_from) ≤ anchor`; frames are read-only; a hash of
  each anchor's view is logged to `ideation.run_manifest`.
- **Future-poison test (T-01).** The test suite replaces all data after the anchor with garbage (inflate / NaN /
  shuffle) and requires universe, market cap, features and labels to be **bit-identical**. A positive control (poisoning
  the past) must change the output, so the test can actually fail.
- **Backtest ban list** (`ideation/lake/bans.py`). In backtest mode the adapter refuses ~31 lake sources that are
  snapshot-in-time or restated (live quotes, `v_latest_*` views, TTM figures, completeness bookkeeping, ...). A static AST
  test also forbids clock reads (`date.today()`, `datetime.now()`) and forbidden imports inside feature/label/universe/
  model code.
- **No restated valuation.** Screener EPS is restated to today's share basis, so valuation is always
  `PIT market cap ÷ fundamental`, never price ÷ EPS. `historical_prices.close` is not raw and is never used for valuation.
- **Purged, embargoed cross-validation.** A training sample is usable for test anchor T only if its `label_end ≤ T`.
- **Sealed holdout** (section 10). The most recent year of labels is physically stored but cannot be read except through
  a loader that raises unless an explicit one-time unseal record exists.
- **Determinism.** Everything is seeded; tree-model prediction uses a single thread; the score parquet's SHA-256 is
  identical across runs and across processes.
- **Vendor estimates** (analyst EPS estimates) are allowed only as a challenger feature on the covered subset and never
  in the core model; their vintage cannot be verified.

---

## 5. Universes: which stocks get scored

Defined in `config/default.yaml` under `universe:`; built by `ideation universe build`.

| Universe | Purpose | Rules (per anchor A) |
|---|---|---|
| **`U_score`** | 1-year model: training and scoring | Stitched security trades in series EQ/BE; **ADV63 ≥ ₹0.2 cr** (zero-filled mean traded value over 63 sessions; ₹0.5 cr is a pre-registered sensitivity); traded in ≥ 50 of the last 63 sessions and at least once in the last 5; listed ≥ 126 sessions; mapped to a company; **PIT market cap ≥ ₹100 cr**; an available annual financial year ending within 730 days; **≥ 60% of core features non-NaN** (the feature-completeness filter, active since P2). No top-N cap. |
| **`U_score_T`** | Tactical (1–3 month) model | Listed ≥ 252 sessions; unmapped and delisted names included; market cap ≥ ₹100 cr where known, otherwise ADV63 ≥ ₹0.5 cr. |
| **`in_u1000` / `in_u500`** | Reporting slices only | Top 1,000 / 500 by trailing turnover; kept to reproduce the earlier coverage study (within ±2 pp) and for survivorship fixtures. |

Every excluded stock gets a recorded exclusion reason, counted per anchor in `ideation.universe_coverage`
(for example `adv_below_floor`, `mcap_below_floor_100`, `too_new`, `no_fy`).

**Scored names per June anchor (U_score after the feature-completeness filter):**

| Anchor | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Names | 666 | 827 | 921 | 942 | 863 | 857 | 1,172 | 1,303 | 1,409 | 1,588 | 1,672 |

The plan expected ≈ 700 (2016), 780 (2019), 1,250 (2022), 1,650 (2025): within ±15% everywhere except 2016 (+18%),
where security stitching across renames adds 105 names. Month-end counts are stored and printed by `ideation coverage`.

**Security stitching.** Symbols change, ISINs change and series move (EQ → BE → BZ). Securities are linked through ISIN,
`nse_symbol_change` (±10 sessions), same-symbol ISIN contiguity (≤ 10-session gap, reuse rejected) and, before mid-2011,
by symbol continuity, with the link method recorded in `ideation.security_map`.

**Trading calendar.** NSE bhav sessions ∪ `^NSEI` dates, with known missing-bhav sessions flagged (2019-09-20..30,
2021-11-04, 2025-02-01).

---

## 6. Labels: what the model predicts

- **Entry** = close of the first session after the anchor. **Exit** = close of the last good session on or before
  `A + h` (h ∈ 1M, 3M, 12M). `label_available_from = label_end`.
- **Prices** are back-adjusted by the project's own routine on the stitched bhav series, using split/bonus/dividend
  factors with `ex_date ≤ anchor` (inside the view), a jump detector for names with missing corporate-action coverage,
  and rights issues left unadjusted but flagged. Cross-checked against `historical_prices.adj_close`: at most 1.88% of
  names differ by more than 5 pp at any unsealed June anchor (T-22).
- **Exits** (what happens to a stock that disappears): distress → −100%; corporate exit (merger/buy-out) → last price,
  then cash; stale → last price; a `last_price` sensitivity is always reported. Unsealed 1Y labels:
  283,734 normal, 2,967 corporate exit, 716 distress, 1,324 stale.
- **Training target** for models: rank-gauss of total return within the universe at each training anchor. Variants
  (`excess`, `xs_rank`) are stored too.

---

## 7. Features

All features are built from a `PitView` and described in flat `config/features_*.yaml` files that the registry
(`ideation/features/registry.py`) loads and validates. Each column declares a `family` (for variance-inflation
selection), a `prior` sign (for sign-constrained models), a `kind` (`cross_sectional` or `broadcast`) and the layouts it
applies to.

**Built (P2): `config/features_fundamental.yaml`, 32 columns** in these groups: valuation yields
(`ep`, `sp`, `ebitda_ev`, `fcf_yield`, `bp`), quality (`roe`, `roe_norm`, `roe_3y`, `roic`, `roa`, margins and margin
volatility), growth (`rev_g1/g3`, `eps_g1/g3`, 5-year growth shown), leverage (`nd_ebitda`), and financials-layout columns
for banks/NBFCs/insurers (`fin_margin`, `cost_ratio`, `dep_g1`). Rules:

- **Annual data only** for the 1Y model (quarterly/TTM figures are broad only from 2023 and are banned in backtests).
- **Layouts and basis rule:** consolidated vs standalone and non-financial vs financial layouts, chosen per name and year.
- **Growth rule:** a growth value is NaN until its history exists; never "first year of data" tricks.
- **Missing-value flags** (`isna_*`, `na_layout_*`) are appended for the models.
- Coverage on `U_score` is ~98% for ROE and 100% for earnings/sales yield; long-history columns are thin early
  (3-year growth starts at the 2018 anchor), which is the history calendar, not a bug.
- Golden values for TCS, HDFCBANK (financials layout), BIOCON, SIEMENS (September year-end) and IDEA (loss-maker with
  negative equity) are pinned in tests.

**Built (PT1): text extraction** (`ideation/text/`). Deterministic and rule-based (no LLM, no network): strips cover
letters and safe-harbour text, splits prepared remarks from Q&A (first Q&A marker with ≥ 500 words before it; 96.1% of
transcripts split cleanly), counts negation-aware positive/negative/uncertainty/litigation/growth terms from a 534-term
lexicon (`LEX_V1`), and extracts numeric guidance percentages. A document becomes available from its parsed filing date
+ 1 day, floored by a month rule. Output: `~/Ideation-data/text/doc_features_LEX_V1.parquet`. Text covers survivors only
(~190 companies with calls in 2016 growing to ~1,300 in 2026), so the PIT features will use within-company changes.

**Planned:**

- **P3** `features_technical.yaml`, `features_events.yaml`: momentum, volatility, drawdown, beta, ownership changes
  (yearly only; quarterly shareholding is live-context until history accrues), macro with release lags, market regime
  (declared `kind: broadcast` because they are the same for every stock on an anchor), relative rotation (RRG), event features.
- **PT2** `features_text.yaml`: company-level PIT text features (levels and changes), plus the "exceeds expectations"
  outcome module (analyst and fundamental surprise).

---

## 8. Models: the pre-registered bake-off

Every model configuration is registered **before** any fit (`ideation trials register`); training refuses an
unregistered config hash. Selection uses a deflated Sharpe ratio / probability-of-backtest-overfitting correction over
the full registered count N, so trying many models does not flatter the winner.

**Learners and fixed hyperparameters** (no tuning; `ideation/models/zoo.py`):

| Key | Model | 1Y settings |
|---|---|---|
| `ols` | Linear regression | — |
| `ridge` | Ridge | α = 10 |
| `scr` | Sign-constrained ridge (features × prior sign, non-negative coefficients) | α = 10 |
| `pridge` | Degree-2 polynomial ridge | α = 100 |
| `rf` | Random forest | 500 trees, min leaf 25, max features 0.33 |
| `lgbm` | LightGBM | 15 leaves, lr 0.03, 400 rounds, feature fraction 0.7, bagging 0.8 |
| `xgb` | XGBoost | depth 4, lr 0.03, 400 rounds, subsample 0.8, colsample 0.7 |
| `mlp` | Neural net (32,16) | mean of 5 seeds; sklearn `early_stopping` banned (random validation split leaks across time) |
| `svr` | RBF support-vector regression | seeded subsample ≤ 6,000 rows |

Tactical settings use stronger regularisation (for example ridge α = 100, RF 400 trees / leaf 100).

**Specifications:** `global` (per-cross-section rank → normal score), `industry` (within-industry z-scores with sign
flips), `hybrid` (both concatenated).

**Phase-I linear family** (from the dossier, `ideation/models/phase1.py`): Unified (faithful and per-cross-section),
Composite with the dossier sub-weights (0.3 value, 0.3 quality, 0.3 growth, 0.1 risk), equal-weight sign-prior
composite, industry equations (walk-forward and in-sample descriptive), walk-forward sign-constrained ridge with
anchor-clustered p < 0.05 selection, best-per-family and iterative VIF < 5, `Final_WFO`, and `PostCOVID`.

**The "90/10" rule** (expected return 90% / stability 10%) is implemented in both of its readings: (a) a per-stock score
blend `0.9·rank(prediction) + 0.1·rank(−σ)` as extra trials, always reported beside the identical config without it;
(b) as the model-selection criterion over trials.

**Trial counts:** 50 one-year trials (`config/trials_1y.yaml`) and 25 tactical trials (`config/trials_tactical.yaml`),
plus benchmarks that are reported but never selectable (12-1 momentum, low volatility, value-only, quality-only,
growth-only, and index / equal-weight / cap-weight universe returns).

---

## 9. From score to recommendation: the ranking mechanism

Implemented as pure functions in `ideation/rank/` (E1); the full written mechanism (`docs/RANKING_MECHANISM.md`) is
rendered from real runs by the research report and is deliberately gitignored.

| Step | What happens |
|---|---|
| R1 | Per-cross-section transform: winsorise, then rank → normal score (or within-industry z). Anchor-constant columns (regime) are declared `broadcast` and standardised on training anchors only. |
| R2 | The model produces a raw score `s_i` per stock. |
| R3 | Percentile rank within `U_score` → **Ideation Score** `IS = round(100·p)` (0–100) and signed score `2p − 1`. |
| R4 | Buckets: deciles D1–D10 (D10 = top), quintiles, tails P99 / P95 / P5 / P1. |
| R5 | **Calibrated expected return** per bucket from out-of-sample history only (anchors strictly before the test anchor), shrunk by 0.5, made monotone with isotonic regression. |
| R6 | **80% conformal band** around the expected return (by decile, time-decayed), with realised coverage reported per anchor. |
| R7 | **Conviction**: High = P95+, Medium = top decile, else Low; notched down for low data confidence, risk flags, regime misalignment, the financials cap (never above Medium); out-of-distribution names are "unscorable". |

Every report prints, per model and horizon, the score → rank → expected-return table, bucket hit rates, spreads and
turnover, and cross-model consensus (votes, unanimous / majority sets, Kendall's W, rank-average ensemble).

---

## 10. Validation protocol and the sealed holdout

- **1Y:** June anchors. Train on the three prior anchors {T−3, T−2, T−1}, test T. Counted test years 2020–2024
  (2019 indicative), plus a March family. **CPCV:** anchors 2016–2024, 9 groups, k = 2 → 36 splits / 8 paths, 1-anchor embargo.
- **Tactical:** monthly anchors from 2011-01, rolling 36-month training, 12-month test blocks, purge by `label_end`,
  embargo 1 month (1M) / 3 months (3M). Counted OOS to 2025-06. CPCV 12 groups, k = 2 → 66 splits (top models only).
- **Statistics:** rank information coefficient (IC) with Newey-West t-statistics, stationary block bootstrap,
  within-anchor-and-industry permutation null, deflated Sharpe ratio, probability of backtest overfitting (CSCV), and a
  shuffled-label control for every learner.
- **Promotion gates** (blocking): IC confidence interval, bucket hit counts, non-inferiority to the Phase-I composite
  baseline, DSR, PBO, leakage and adversarial tests green. Promotion is manual (`train --promote`); the daily job never promotes.
- **Sealed holdout:** 1Y anchors from 2025-03 and tactical 2025-07 onward are stored in `ideation.label` but readable only
  through `ideation.labels.loader.load_labels`, which raises `SealedAnchorError` unless `ideation.holdout_unseal` has a
  row for the model family. The unseal happens **once**, after the champions are frozen, and the result is reported
  separately and never fed back into selection.

---

## 11. Portfolios, capital tiers and the single-stock pick (planned)

Not built yet (phase P7). Decisions already fixed:

- **Black-Litterman** weights from the model's calibrated views; default profile *Balanced* (all four profiles available).
- **Capital tiers:** model portfolios for **₹10,00,000 / ₹1,00,000 / ₹10,000** with cardinality scaled to capital
  (≈ 3–6 names at ₹10k), integer-share allocation by mixed-integer programming, and a realistic retail cost model
  (STT, exchange and SEBI charges, stamp duty, GST, DP charge ≈ ₹15.93 per scrip per sell day).
- **Single-stock pick for ₹1,000 / ₹5,000 / ₹10,000:** among High/Medium-conviction liquid ideas affordable at one share
  or more, ranked by `(μ − Rf)/σ` of the single asset, with the cost drag at tiny capital stated explicitly and a mandatory
  concentration warning plus a backtested outcome distribution (median, P10, P90, probability of loss).
- Financials (banks/NBFCs/insurers) are scored but conviction-capped at Medium. Sector caps are off, with a warning
  above 35% in one sector.

---

## 12. Setup

Requires macOS or Linux, [`uv`](https://docs.astral.sh/uv/), Python 3.12, and a populated local lake (the Scraper).
On macOS, LightGBM/XGBoost need `libomp` (`brew install libomp`; `doctor` checks it).

```sh
uv sync                                   # Python 3.12, exact pins from uv.lock
git config core.hooksPath .githooks       # pre-push: secret/data scan + offline tests
cp .env.example .env                      # optional; IDEATION_* variables, see section 16
uv run ideation bootstrap-role --yes      # ONE-TIME, as the lake superuser: role ideation_rw (idempotent)
uv run ideation init-db                   # apply ideation-schema migrations (idempotent, checksummed)
uv run ideation doctor                    # lake access, read-only, RLS bypass, schema, lags, freshness
uv run pytest -m "not lake"               # offline suite;  -m lake  runs against the local lake
```

The first offline test run in a brand-new virtualenv is slow (~2.5 minutes) while Python compiles the bytecode of
numpy/scipy/scikit-learn; warm runs take ~2 minutes for the whole offline suite.

---

## 13. Command reference

Run everything with `uv run ideation <command>`. Only the checkout on `main` applies migrations.

| Command | What it does | Typical runtime |
|---|---|---|
| `bootstrap-role --yes` | One-time: create role `ideation_rw`, grants, schema `ideation` | < 1 s |
| `init-db` | Apply pending `sql/ideation/*.sql` in order | ~1 s |
| `doctor` | Health check: identity, read-only enforcement, grants, RLS bypass, schema version, lag pins, lake freshness, libomp, hooks | ~2 s |
| `snapshot --full` / `--delta` | Pull the lake into a parquet snapshot in one `REPEATABLE READ READ ONLY` transaction, with `available_from` on every dated row and a manifest | 195 s full; delta ~12 s |
| `universe build --anchors june,march,monthly [--force]` | Build `U_score`, `U_score_T`, `in_u1000` per anchor into `ideation.universe_member` / `universe_coverage` | ~5 min |
| `labels build --horizon 1Y,3M,1M --anchors … [--force]` | Forward-return labels with exit handling into `ideation.label` and parquet | ~3.5 min |
| `coverage [--anchors …]` | Print scored-N per anchor, exclusion reasons and the earlier-study reproduction | seconds |
| `bias-block` | Print the mandatory Data & Bias block with this snapshot's measured numbers | seconds |
| `features build --anchors june,march [--force]` | Build PIT fundamental features and the coverage table | ~90 s |
| `text extract --types concall_transcript,concall_ppt` | Extract text signals from transcripts and presentations | 272 s cold, 13 s warm |
| `trials register --family …` / `list` / `check` | Register, list and verify pre-registered trial configs | seconds |
| `runs list` / `runs show` | Inspect the run registry (idempotency by `run_key`) | seconds |

**Rebuild order** after a new snapshot or a feature change: `snapshot` → `features build` → `universe build --force` →
`labels build --force`. Features must precede the universe because the feature-completeness filter reads them.

Not yet available: `train`, `backtest`, `holdout unseal`, `ideas`, `portfolio`, `pick`, `chat`, `serve`, `daily`.

---

## 14. Repository layout

```
ideation/
  cli.py, config.py, logs.py, runs.py, repro.py, doctor.py
  lake/        adapter (read-only), queries (named queries only), bans, snapshot, symbols, prices
  pit/         lags (availability), overrides (COVID table), calendar, contract (PitStore / PitView)
  universe/    build, mcap (PIT market-cap hierarchy), coverage, cli
  labels/      build, loader (holdout sealing), exits_report, cli
  features/    registry, fundamental, transform, build, cli
  text/        reader, clean, lexicon, extract, cli
  cv/          walkforward, cpcv, cscv
  eval/        metrics, inference, bootstrap, dsr, pbo, selection, consensus_math, bias_block
  models/      zoo, phase1, select_vif
  rank/        transform, percentile, buckets, calibrate, conformal, conviction
  trials/      registry, cli
  db/          writer (guarded writes), migrate, bootstrap, identity
config/        default.yaml, features_fundamental.yaml, trials_1y.yaml, trials_tactical.yaml
sql/bootstrap  000_role.sql (one-time, superuser)
sql/ideation   001–003, 010–012 migrations (numbers 004–009 reserved for P1, 013–014 for E1)
tests/         unit/, leakage/, lake/
scripts/       secret_scan.py
.githooks/     pre-push (secret scan + offline tests)
docs/plan/     00–06 binding design documents;   docs/STATUS.md   build log
```

---

## 15. Database objects

Schema `ideation` (all owned by `ideation_rw`):

| Group | Tables |
|---|---|
| Infrastructure | `schema_migration`, `run`, `run_manifest`, `snapshot`, `experiment_trial` |
| PIT foundation | `calendar`, `security_map`, `universe_member`, `universe_coverage`, `label`, `holdout_unseal` |
| Modelling | `feature_set`, `model`, `wf_result`, `prediction`, `oos_prediction`, `rank_calibration`, `outcome` |
| Later phases (created, empty) | `regime_snapshot`, `sector_rotation`, `idea`, `portfolio`, `portfolio_weight`, `backtest`, `backtest_period`, `chat_session`, `chat_message` |

Migration ranges per phase: P1 003–009, E1 010–014, P2 015–019, PT 020–024, P3 025–029, model sprint 030–039, ideas
040–044, portfolio 045–049, chat 050–054, API/daily 055–059. An applied migration file is never edited; the migrator
verifies checksums and refuses drift.

---

## 16. Configuration

- **Environment** (`.env.example`): `IDEATION_*` variables via pydantic-settings, including `IDEATION_DATA_DIR`
  (default `~/Ideation-data`), `IDEATION_DOCUMENTS_ROOT` (concall documents), the lake DSN and an optional
  `IDEATION_SECRETS_FILE` (default `~/Ideation-data/.secrets.env`, mode 600, never read by the code paths here).
- **Methodology** lives in `config/*.yaml`. Every file is merged by file stem and hashed into a `config_hash` recorded on
  every run, so a changed assumption is visible and a result is reproducible from its recorded hash.
- **Not configurable on purpose:** the statutory/PIT lags (`pit/lags.py`, `pit/overrides.py`) and the backtest ban list
  are code constants pinned by tests, so they cannot drift through config.

---

## 17. Testing and quality gates

Three test markers, assigned by directory: `unit` (pure logic), `leakage` (the anti-leakage and statistical-correctness
suite), `lake` (needs the live database; skipped cleanly when unreachable).

Every phase must pass before merge:

```sh
uv run ruff check .
uv run pytest -m "not lake"        # 510 passed (1 skipped, 1 expected failure, 1 unexpected pass)
uv run pytest -m lake              # 86 passed, DDL tested in rolled-back transactions (ddl_sandbox)
uv run python scripts/secret_scan.py --all
uv run ideation doctor
```

The leakage suite includes: future-poison tests on prices, universe, market cap, fundamentals and labels; shuffled-label
controls for every learner; planted-signal recovery; a planted-leak test that must be blocked; purge/embargo boundary
tests; preprocessing-fit-only-sees-train tests; bit-reproducibility of every learner; the holdout-sealing test; the static
import/ban scans. The two expected booster results are documented in the E1 section of `docs/STATUS.md`.

The pre-push hook rescans every pushed commit and the work tree for secrets and data files, then runs the offline suite.

---

## 18. Safety rules

- **Never write to `equity` or `macro`**, never touch Supabase, `~/Scraper - Screener.in` or `~/X-Agent`. The database role
  has no write grants on the lake, and the code write guard rejects any target outside `ideation.*`.
- **Lake reads only through the adapter** using named queries; the session is read-only with a statement timeout.
- **No data or secrets in git:** no `.env`, `*.db`, `*.sqlite*`, `*.parquet`, `*.csv`, `.hypothesis/`, reports, logs or key files;
  the secret scanner (also a pre-push hook) blocks them, including in history.
- **Sealed labels** are read only through `load_labels`.
- **Model promotion is manual**; the scheduled job never promotes.

---

## 19. Working on the project (phases, worktrees, agents)

Development is phase-based. Each phase has a card in `docs/plan/05_MODEL_SPRINT.md` §10.3 with acceptance criteria.

- At most two implementation agents run at once, each in its own git worktree from up-to-date `main`:
  `git worktree add ../wt-<phase> -b phase-<id>-<slug> main`.
- A branch tests migrations in the `ddl_sandbox` fixture (`BEGIN; apply pending files; yield; ROLLBACK`) and **never runs
  `init-db`**; after merge, `main` runs `uv run ideation init-db`.
- Branches commit work-in-progress at every green milestone; the orchestrator verifies (ruff, both test suites, secret
  scan), merges in dependency order, and pushes.
- Every phase appends a STATUS entry with commands, runtimes and measured results.

---

## 20. Known limitations and residual biases

These are disclosed in every report's Data & Bias block.

- **Restated financials.** Screener data are restated; point-in-time availability is modelled by statutory deadlines, not
  original filing vintages.
- **Current sector labels.** Industry/sector classifications are today's, applied historically.
- **Partial survivorship.** The lake is richer than most (delisted, BZ-series and corporate-exit names are included and
  tested), but not all history is recoverable; text coverage is survivors-only.
- **Missing dividends** in labels for names without corporate-action coverage (~7% of 1Y June labels inside `U_score`);
  rights issues are unadjusted (flagged); yfinance cross-check data themselves miss some 2023+ splits.
- **Short history:** fundamentals are broad only from FY2015, so the 5-year-window variant of a Phase-I model is
  infeasible; counted 1Y test years are only 2020–2024.
- **Date granularity:** document dates are month-level, so a concall becomes usable from the start of the following
  month at best.
- **Weak-signal handicap** of the fixed boosted-tree settings (section 2.3).
- **Holdout disclosure:** during Phase 1, before the sealed loader existed, scratch scripts printed some June-2025 1Y
  label statistics (universe median, exit counts, one cross-check share). Nothing was fitted or selected on them.
- **Sealed labels exist physically** in `ideation.label` and the labels parquet; the seal holds only if every consumer
  goes through the loader (a static check banning direct reads is not yet built).
- A name that is an IPO within the data's first 12 months is treated as new, so its growth is NaN for that window.

---

## 21. Documentation map

Read in this order:

1. [`docs/plan/03_RECONCILED_DECISIONS.md`](docs/plan/03_RECONCILED_DECISIONS.md): binding owner decisions and conflict resolutions.
2. [`docs/plan/05_MODEL_SPRINT.md`](docs/plan/05_MODEL_SPRINT.md): binding addendum (universes, ranking mechanism, model
   bake-off, consensus, text/event features, holdout, phase DAG). Overrides 03 where they differ.
3. Plan document 06 in [`docs/plan/`](docs/plan/) (past-quant-work digest): the owner's original modelling.
4. [`docs/plan/01_ARCHITECTURE_PLAN.md`](docs/plan/01_ARCHITECTURE_PLAN.md): architecture, features, ideas, portfolio, chat.
5. [`docs/plan/02_VALIDATION_PLAN.md`](docs/plan/02_VALIDATION_PLAN.md): leakage and validation (the PIT contract, tests T-01…T-26).
6. [`docs/plan/04_EXPERT_SIGNALS.md`](docs/plan/04_EXPERT_SIGNALS.md): planned live-only expert-signal filter.
7. [`docs/plan/00_CONTEXT_BRIEF.md`](docs/plan/00_CONTEXT_BRIEF.md): original context brief.
8. [`docs/STATUS.md`](docs/STATUS.md): per-phase build log with commands, runtimes, measured numbers, deviations, risks.
9. [`CLAUDE.md`](CLAUDE.md): rules for agents working in this repo.
