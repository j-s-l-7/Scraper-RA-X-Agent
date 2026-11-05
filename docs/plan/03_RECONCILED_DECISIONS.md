# Reconciled decisions (binding) — read this FIRST

This file reconciles `01_ARCHITECTURE_PLAN.md` (architect) and `02_VALIDATION_PLAN.md` (leakage/validation red-team).
**Where they conflict, this file wins.** Where this file is silent, follow 01 for structure/features/ideas/portfolio/chat and
02 for PIT contract, universe, labels, CV, statistics, audits, tests and bias disclosure.

Date: 2026-11-05. Owner decisions recorded in §A. Verified facts used to break ties in §B.

---

## A. Owner decisions (final)

| # | Decision |
|---|---|
| A1 | Repo `j-s-l-7/Scraper-RA-X-Agent` is **private**. No data, secrets, `.env`, `*.db`, `*.sqlite*`, `*.parquet`, `*.jsonl.gz`, reports or logs are ever committed. |
| A2 | Outputs live in a new **`ideation` schema in the local lake** (`localhost:5433/equity_lake`). `equity`/`macro` are read-only. Supabase is never touched. |
| A3 | **Dedicated role `ideation_rw`** (created in Phase 0 by a one-time superuser script). See §C1. |
| A4 | Scope: **stock ideas (core) + Black-Litterman portfolio construction**. Holdings review (Phase IV nudges) and mutual funds are out of scope for this milestone. |
| A5 | Horizons: **1Y** and **tactical 1–3M** (primary 3M = 63 sessions; 1M reported). |
| A6 | Default profile: **Balanced**. All four profiles available on demand and in backtests. |
| A7 | **Capital tiers:** model portfolios for **₹10,00,000, ₹1,00,000 and ₹10,000**, plus a **single-stock pick for ₹1,000 / ₹5,000 / ₹10,000**. Integer shares, real retail costs. See §D. |
| A8 | Financials (banks/NBFCs/insurers) are **included** in the global 1Y model with financial-layout columns; **conviction capped at Medium**; descriptive sign-prior composite shown alongside. |
| A9 | Delisting/exit policy per 02 §3.3 (distress −100%, corporate exit = last price, stale = last price), with `last_price` sensitivity. |
| A10 | Sector caps off (dossier §5.6); warning at > 35% sector weight. |
| A11 | Chat LLM: Anthropic Claude (default `claude-fable-5-1`, configurable). Implementers MUST load the `claude-api` skill before writing any Anthropic SDK code. |
| A12 | Model promotion is **manual** (`train --promote` only after gates pass); `daily` never promotes. |

## B. Verified facts that break ties (probed 2026-11-05)

- B1. `equity.shareholding`: frequency **Y** broad from **2017** (1,754 cos), **Q only from 2023** (2,250+). ⇒ trainable ownership features use **yearly** changes (`prom_chg_1y`, `fii_chg_1y`, `dii_chg_1y`, from the 2018 June anchor); quarterly changes are **live context only** until enough history accrues.
- B2. `historical_prices.close` is **not raw** (yfinance-adjusted with an extra factor; RELIANCE 2016 238.9 vs raw 1,045.2). Never use it for PIT valuation. Labels use own adjustment (02 §3.2) and are cross-checked against `historical_prices.adj_close`.
- B3. Quarterly/TTM fundamentals broad only from 2023 ⇒ **1Y model is annual-only**; TTM banned in backtests.
- B4. Screener EPS is restated to today's share basis ⇒ never price/EPS. Valuation = `mcap_pit / fundamental`.
- B5. 57 lake tables have **row-level security enabled** (no policies). Superuser bypasses RLS; a normal role would silently read **zero rows**. ⇒ `ideation_rw` needs `BYPASSRLS` (§C1).
- B6. `pg_hba`: trust on localhost ⇒ `ideation_rw` connects without a password; no secret needed for the lake DSN.
- B7. Host: Intel macOS x86_64, `uv` 0.9.28, Python 3.12 available.

## C. Conflict resolutions

**C1. DB role (Phase 0, `sql/bootstrap/000_role.sql`, run once as superuser via `ideation bootstrap-role`):**
```sql
create role ideation_rw login nosuperuser nocreaterole nocreatedb bypassrls;
grant connect, create on database equity_lake to ideation_rw;
create schema if not exists ideation authorization ideation_rw;
grant usage on schema equity, macro to ideation_rw;
grant select on all tables in schema equity, macro to ideation_rw;
alter default privileges for role brainx in schema equity grant select on tables to ideation_rw;
alter default privileges for role brainx in schema macro  grant select on tables to ideation_rw;
grant execute on all functions in schema equity to ideation_rw;   -- read-only SQL functions only
```
Idempotent (guard with `do $$ … if not exists … $$`). Both DSNs connect as `ideation_rw`. The read path additionally sets
`default_transaction_read_only=on` and `statement_timeout`. The code write-guard (01 §1.1) stays as defence in depth.
A lake test proves `insert into equity.company …` as `ideation_rw` fails with **permission denied** and that
`select count(*) from equity.listing_history` returns > 0 (RLS bypass works).

**C2. PIT market cap — use 02 §1.3 hierarchy, not 01 F3.** Order: **A** (raw bhav close × yfinance shares as-of ≤ anchor within 400d,
corrected by split/bonus factors with ex_date in (as_of, anchor]) → **B1** (split-only-adjusted close on today's basis × standalone NP/EPS
of the latest available FY) → **B2** (raw close × Equity Capital ×1e7 / FV). Persist `mcap_src`, `mcap_flag` (methods disagree > 25% ⇒ median + flag).
*Basis-conversion exemption:* B1/B2 need post-anchor split/bonus factors purely to convert units of an already-observable quantity.
These factor rows are tagged `role='basis_conversion'` and are **exempt from the future-poison test** (T-01); every other future row
is poisoned. T-21 (A vs B1 consistency: median abs err < 2%, ≥ 85% within 10%) guards the conversion. Valuation never uses price/EPS.

**C3. Label timing (overrides 02 §3.1 exit and 02 T-07/T-08 expectations).** Entry = close of `s1(A)` (first session after A).
**Exit = close of the last good session on or before `A + h`** (calendar months), so a 1Y label from anchor T−1 ends at T's close.
`label_available_from = label_end` (EOD data is usable after the close, consistent with 02's bhav rule). Purge: train sample usable for
test anchor T iff `label_end ≤ T`. ⇒ **1Y June walk-forward trains on {T−3, T−2, T−1}** (the dossier's 3y-train / 1y-test), the freshest
regime. Counted test years **Jun-2020 … Jun-2025 (6)**; Jun-2019 indicative (its 2016 training anchor lacks 3Y features).
Update T-07: test 2022 ⇒ train {2019, 2020, 2021}; any sample with `label_end > T` absent. T-08 analog for monthly: train anchors with
`label_end > first test anchor` absent. CPCV embargo per 02 §4.5 unchanged.

**C4. Universe — 02 §2.1** (ADV63 zero-filled mean, ≥ 50/63 sessions, traded in last 5 sessions, top 1,000; `listed_sessions` ≥ 126 for 1Y,
≥ 252 for tactical; scorable rules and exclusion reasons as in 02). 01's ADV126-median variant is not used.

**C5. Prices/labels — 02 §3.2** (own adjustment on the stitched bhav series with `adj_factor` split/bonus/dividend factors with
ex_date ≤ anchor inside PitView; jump detector for uncovered names; rights unadjusted + flagged). Not `historical_prices.adj_close`
(used only for the T-22 cross-check).

**C6. Standardization.** Primary 1Y input transform = 01's per-cross-section **rank → normal score ("global")** (dossier's selected variant).
Within-industry-group z-score with sign flips (02 §4.6) is a **pre-registered trial**. Growth cleaning `winsor` (01) vs `dossier_median` (02)
is a pre-registered trial. Every variant is logged in `ideation.experiment_trial` and counts toward DSR N.

**C7. Growth-missing rule — 02 §4.1** (prior-FY validity + NaN for FYs ending within 12 months after the stitched listing date).
Not "first year of data".

**C8. Tactical CV — 02 §4.4**: monthly anchors from 2011-01 (needs 252 listed sessions), rolling **36-month** train, 12-anchor test block,
purge per C3, embargo 1 month (1M) / 3 months (3M). Regime features are a tactical feature set (01 §3.6), kept only if they win.

**C9. Selection vs promotion.** Selection among trials = dossier 90/10 (`0.9·z(mean OOS IC) + 0.1·z(stability)`, 02 §6.6). Promotion =
**02 §5.5 gates** (blocking: IC CI, bucket hit-count, non-inferiority vs Phase-I composite baseline, DSR, PBO, leakage + adversarial green).
If the RF fails, **the composite baseline ships** and reports say so. Failing gates is an allowed outcome, never a phase failure.

**C10. Package layout** = 01 §9 plus 02's modules: `ideation/pit/` (contract.py `PitStore/PitView`, lags.py, overrides.py),
`ideation/universe/`, `ideation/labels/`, `ideation/cv/` (walkforward.py, cpcv.py), `ideation/eval/` (metrics.py, inference.py,
dsr.py, pbo.py, bias_block.py). Import contract (import-linter or an AST test): `features`, `models`, `universe` must not import
`lake`, `labels` (except models' training entry), `psycopg`. Feature builders take a `PitView`, never a connection.

**C11. c_i** = 02 §1.4 PIT form in backtests; live adds dq/completeness (01 §5). Conformal width may feed Ω (option, off by default).

**C12. Costs.** IC/spread research uses the dossier's 12 bps/side. **Portfolio outputs and portfolio backtests use the retail
cost model** (§D2) because capital tiers are small.

## D. Capital tiers and the single-stock pick (new; Phase 7)

**D1. Model portfolios at ₹10L / ₹1L / ₹10k** (`ideation portfolio --capital 1000000|100000|10000 --profile balanced`):
1. Continuous BL weights per 01 §6 (π and Q same units; OOD ⇒ size 0).
2. **Cardinality by capital:** `K = min(profile_target_names, floor(C / min_ticket))`, `min_ticket = max(₹1,000, fixed_sell_cost / max_fixed_cost_pct)`
   (default `max_fixed_cost_pct = 1%` ⇒ ~₹1,600 with a ₹15.93 DP charge). Keep the top-K names by continuous weight and re-solve BL restricted to them.
3. **Discrete allocation** with `scipy.optimize.milp` (HiGHS): integer shares `n_i ≥ 0`, minimize Σ|n_i·p_i − w_i·C| + λ_c·cash, subject to
   Σ n_i·p_i + buy costs ≤ C, per-name cap, and `n_i·p_i ≥ min_ticket·b_i` with binary `b_i`. Names with price > cap·C drop out before the solve.
4. Output: shares, ₹ amount, weight, cash residue, expected return/vol, β, est. round-trip cost in ₹ and %, and an explicit
   **"diversification at this capital"** note (effective N). At ₹10k expect ~3–6 names.

**D2. Retail cost model** (`config/costs.yaml`, defaults to verify with the user's broker, all overridable): delivery brokerage ₹0 (or ₹20/order),
STT 0.1% buy and sell, NSE transaction 0.00297%, SEBI ₹10/crore, stamp duty 0.015% on buy, GST 18% on brokerage+exchange+SEBI,
DP charge ₹15.93 per scrip per sell day. Used in discrete allocation, rebalance tests and capital-tier backtests.

**D3. Single-stock pick for ₹1k / ₹5k / ₹10k** (`ideation pick --capital 5000 [--horizon 1y]`):
- Eligible: 1Y long ideas with conviction ≥ Medium, not OOD, no risk flags, liquid (ADV20 ≥ ₹2 cr), price ≤ C (≥ 1 share).
- Rank by **single-asset risk-adjusted posterior**: `(μ_BL,i − Rf) / σ_i` (a lone stock carries all idiosyncratic risk, so volatility must
  be penalised), tie-break by cash residue (prefer ≤ 20% uninvested) then liquidity.
- Output: the pick (+2 runners-up), shares, ₹ invested, cash left, round-trip cost % (fixed DP cost dominates at ₹1k—say so),
  horizon, conviction, expected band with backtested hit rate, key risk, invalidation, and a mandatory concentration warning. A
  "diversified alternative" line lists the cheapest liquid Nifty 50/500 index ETF from bhav (configurable symbol list; informational only).
- Backtest: `backtest --kind pick --capital …` replays the rule at each June anchor (OOS predictions only) and reports the distribution of
  1Y outcomes (median, P10, P90, P(loss), P(< −20%)) vs a random-eligible-stock null — so the user sees how risky a single pick is.

## E. Final phase plan (one Opus agent per phase; sequential; branch → tests → merge to main → push)

| Phase | Content | Key acceptance |
|---|---|---|
| **P0** | Scaffold (uv, py3.12, pyproject, ruff, pytest markers `unit/leakage/lake`), `.gitignore`, `.env.example`, `scripts/secret_scan.py` + `.githooks/pre-push`, config (pydantic-settings + YAML), logging, run registry, `bootstrap-role` (C1), `init-db` migrations (01 §1.3 DDL + 02 tables: `run_manifest`, `experiment_trial`, `calendar`, `security_map`, `universe_member`, `universe_coverage`, `label`), lake adapter (RO, statement_timeout, backtest-mode ban list), `pit/lags.py` + overrides, `doctor` | 01 P0 acceptance + C1 tests + 02 T-10..T-14, T-17 (scaffold), T-19 (trivial) |
| **P1** | Snapshot to parquet with `available_from` on every row + manifest; calendar; security stitching; PIT universe + coverage; PIT mcap hierarchy (C2); own price adjustment + jump detector; labels + exits (C3); `PitStore/PitView` guard + `poison_after`; bias-block skeleton | 02 P1 row (T-01 prices, T-03, T-15, T-16, T-20..T-23), coverage table ±2 pp of 02 §0.1, exit counts ±5% of 02 §0.2 |
| **P2** | Fundamental features: `config/feature_map.yaml` (01 §2.3/2.4 with C2 valuation), layouts, basis rule, availability via lags (+event_date later-wins), growth rule (C7), transforms (C6), c_i PIT | 01 P2 acceptance (golden values, registry validation), T-01 on fundamentals, coverage per anchor |
| **P3** | Technical + ownership (B1) + regime + RRG (EW industry-group composites) + macro with lags | 01 P3 acceptance, 02 P2 row |
| **P4** | 1Y: WFO (C3), CPCV, metrics/inference/DSR/PBO, trials registry (pre-registered grid), bake-off (rf, ridge, lgbm, composite baseline), audits (adversarial B, OOD, H, clustered PI, ALE), selection (C9), report with bias block | 01 P4 + 02 P3 rows; gates printed honestly |
| **P5** | Tactical 1M/3M: monthly WFO + CPCV, T0/T1 tiers, regime on/off, `mom_12_1` baseline | 01 P5 + 02 P4 rows |
| **P6** | Predict + ideas engine (01 §5), calibration (01 §4.3), conformal (02 §6.3), OOD size-0, explanations, X-Agent overlay (read-only sqlite, live-only), continuity, markdown | 01 P6 + 02 P5 rows |
| **P7** | BL portfolio (01 §6) + capital tiers & discrete allocation (D1) + retail costs (D2) + single-stock pick (D3) + portfolio/pick backtests + block bootstrap | 01 P7 + 02 P6 rows + D-tests (integer feasibility, budget ≤ C incl. costs, K by capital, pick eligibility) |
| **P8** | Chat CLI (RA pattern; `claude-api` skill first), tools incl. `build_portfolio(capital)` and `pick_stock(capital)`, equity guardrails, compliance + untraced numbers, evals | 01 P8 + 02 P7 rows |
| **P9** | FastAPI (127.0.0.1, SSE), `daily`/`weekly` chains with freshness gate, launchd templates, reports bundle, README/CLAUDE.md final | 01 P9 + 02 P8 rows |

Every phase: `ruff check` clean; `pytest -m "not lake"` and `pytest -m lake` green on this machine; `scripts/secret_scan.py --all` passes;
append a **Status** entry (commands run, runtimes, measured results) to `docs/STATUS.md`; commit on `phase-N-<slug>`; the orchestrator merges to `main` and pushes.
