# 06_PAST_QUANT_WORK_DIGEST.md

This digest covers the 7 Past-Quant-Work PDFs (text extraction confirmed complete: none of the PDFs contain embedded images) and all 27 notebook photographs (IMG_1259–1272 and IMG_1274–1286; IMG_1273 is absent). It cross-checks them against `Quant_Equity_System_Dossier_v2.md`.

Conventions used throughout:
- **[unclear]** means illegible or ambiguous in the source.
- **[not specified]** means the sources are silent.
- **[dossier-only]** means the claim appears in the dossier but not in any source.
- Page refs: `DM p.N` = Quant_Model_Data_Matrix; `RES p.N` = Results; `MR p.N` = Models_Results; `REG` = Regression_Eq_Sense; `AoB p.N` = Art_of_Backtesting-2; `BL §N` = Black-Litterman Upgrade v1.0; `PLAT` = Platinum Std. Upgrades; `NB-12xx` = notebook image.

---

## 0. Source map

| Source | Content relevant to modelling |
|---|---|
| DM p.2 | "Overall Dataset Correlations" (Pearson/Spearman of "Fundamental Composite Eq" vs 1/2/3/5-yr forward returns) |
| DM p.4–8 | 32 industry equations (summary list: N, Spearman, equation) |
| DM p.12–48 | "Complete Industry Analysis". Selection rules, per-industry exact weights, per-metric Spearman, metric "Type", rolling year-by-year Spearman vs 1–5 yr returns |
| RES p.2 | WFO quintile run (with / without COVID windows), CSV rows. Author's IC→probability note |
| RES p.3 | "Tier 1" metric recommendation (P/S, EV/EBITDA, P/E, P/FCF, all negative weight) |
| RES p.5–6 | Data source, coverage by year, base and derived metric list with category and weight constraint |
| RES p.8–19 | Standalone WFO (constrained Ridge): config, summary, per-window selected and non-selected features with p-values/VIF, equations, aggregate table |
| RES p.21–33 | Per-metric Spearman vs 1-yr forward return by year, with Q1/Q5 within-bucket corr, and overall ranking |
| RES p.35–46 | Same for 2-yr forward returns |
| RES p.48–51 | Growth-metric outlier report and recommended cleaning |
| MR p.2–4 | 7-model × 12-year Spearman table, summary stats, Composite_3Y corrections, source scripts |
| MR p.6–7 | Exact equations: Unified, legacy Composite (with sub-weights), Post-COVID WFO, Final WFO |
| REG | Banking APT regression template. Explicitly "a refined LLM output… yet to be structured"; all statistics illustrative |
| AoB | WFO window schedules (2/3/5-yr), priorities, efficacy tests, stitching, Monte Carlo design, metric thresholds, work schedule |
| BL | BL v1.0 spec: data inputs, stock composite Sᵢ, EWMA, view mapping, posterior, weights, nudges, turnover cap, batch, monitoring, CI harness |
| PLAT | 9-row "Platinum Standard" critique/upgrade table |
| NB-1259 | Phase II report outline: 7-learner bake-off, 90/10 → stability, regime features 2007–2025, IC chain 0.16→0.198/0.182→0.23→0.25, "(3) Volatility(Momentum)+Market Regime+Market Cap must move together" |
| NB-1260 | Index–stock divergence; before/after-features table; quantile table (Q5/D10 beats NIFTY, L/S); "90/10 inconsistency"; "inconsistent base" |
| NB-1261 | Year-by-year comparison; "Feature impact summary (corrupted by 90/10 methodology)"; RF-hybrid 0.21 vs 0.05; model-lineage diagram; "+ve years 93% → ~100%, revisit"; "Remove Section 12: expanding vs rolling 3y WFO" |
| NB-1262 | Quantile selection on updated counts; "titan features" thesis; IC chain 0.16 (low N) → 0.182 (lower N) → 0.223 (Raw) → 0.233 (after features + 90/10) → 0.25 (+90/10, before features) |
| NB-1263 | High-conviction (D10, P95, P99) and bottom-bucket (Q1, D1, P5, P1) median tests; critical-year comparison → drop new features; "Mkt-cap constraints only ruin the model's ability" |
| NB-1264 | Market-cap-conditioned quantile matrices (count-based); Platinum Audit = H-stat, adversarial validation, OOD; "specify what is hybrid approach" |
| NB-1265–1267 | Code architecture (BLM views service, PAP config/blm/analysis/alpha_model/core, models, backtesting) |
| NB-1268 | BLM architecture: CAPM prior inputs, configs (Rf 7%, Rm 12%), backtest integrations (nudges, protection period, RF penalty), Ω = scaling factor (τ × variance) via data-completeness proxy |
| NB-1269 | Style constraints; constraint params; β spec; τ (fixed 0.05 vs adaptive); Ω; ridge 1e-5; Ledoit-Wolf; OAS |
| NB-1270 | Config values (12 bps, 20% cap, 25% per rebalance, risk aversion 2.5/1.5); eigen-analysis; T/N = 1.68 |
| NB-1271 | RF penalty (<18% → negative); evaluation metrics; folder map; covariance/Ω/turnover mind-map |
| NB-1272 | "Review": RF best; IC ≈ 0.223 (median 0.232), N 1,100–1,500 of 2,000; fails 2011 & 2018; new features = noise; 90/10 definition; win rates 69/73/74/73; benchmark-beat 61/70/72/75; probability confusion matrix |
| NB-1274, 1277, 1278, 1279 | Live-management decision logic (security/sector/NIFTY matrix, avg price, profiles, breakouts, rebalance alpha) |
| NB-1275–1276 | Performance evaluation (MWROR/TWROR, Sharpe limits, Treynor, IR, M², RAP, risk-aversion mapping) |
| NB-1280 | Implementation plan; "constraints on n(trades) yearly & turnover 25%" |
| NB-1281 | Analysis-package responsibilities; "Ω low so RF dominates (95% weight)"; efficient frontier 50 pts |
| NB-1282 | Hand derivation of the BL posterior; **Ω = 3 × Pᵢ(τΣ)Pᵢ′**; "(Σ/T + Ω) Incorrect" |
| NB-1283 | Optimiser component flow; "Calculate Ω (He–Litterman) 3×Pᵢ′(τΣ)Pᵢ"; objective max(return − λ·risk²) + profile trade limit |
| NB-1284 | Risk-aversion options; RF-penalty fix; trade limits (3 or 25% per rebalance; yearly unspecified); diversification recommender (RRG, 4 pillars) |
| NB-1285 | Config: τ = 0.025/0.05; risk aversion 2.5/1.5; micro-cap 500 Cr; market-cap and sector caps **removed**; efficient frontier 50 points |
| NB-1286 | Synthetic-portfolio harness (10 steps); harness composite 0.4/0.3/0.2/0.1 |

---

## 1. Data & universe (as used originally)

### 1.1 Phase I panel (RES p.5)
- File `India_valuation.csv`: 7,000 observations, 500 unique tickers, 2010-03-31 to 2025-03-31, annual (31 March anchor).
- **Years 2016 and 2017 are missing** from the dataset. 2025 has no forward returns (test-only).
- Coverage by year:

| Year | Tickers | Valid Price | Valid Returns | Has Metrics | Coverage % |
|---|---|---|---|---|---|
| 2010 | 500 | 300 | 300 | 308 | 60.0 |
| 2011 | 500 | 309 | 309 | 327 | 61.8 |
| 2012 | 500 | 319 | 319 | 338 | 63.8 |
| 2013 | 500 | 323 | 323 | 373 | 64.6 |
| 2014 | 500 | 327 | 327 | 406 | 65.4 |
| 2015 | 500 | 330 | 330 | 431 | 66.0 |
| 2018 | 500 | 344 | 344 | 437 | 68.8 |
| 2019 | 500 | 361 | 361 | 452 | 72.2 |
| 2020 | 500 | 387 | 387 | 460 | 77.4 |
| 2021 | 500 | 400 | 396 | 475 | 79.2 |
| 2022 | 500 | 408 | 407 | 477 | 81.4 |
| 2023 | 500 | 421 | 414 | 481 | 82.8 |
| 2024 | 500 | 432 | 423 | 482 | 84.6 |
| 2025 | 500 | 436 | 0 | 476 | 0.0 |

Coverage % = Valid Returns / 500. The dossier omits the "Valid Price" column.

- **Forward-return construction is inconsistent between artifacts.** In the Data Matrix industry tables, forward returns are N/A whenever t+k lands in 2016 or 2017:
  - 2013: 3y and 4y are N/A
  - 2014: 2y and 3y are N/A
  - 2015: 1y and 2y are N/A
- Yet RES shows 330 valid returns for 2015 and runs a WFO test on 2015 (N = 289).
- RES's per-metric 1-yr correlation reports start at 2011 (2010 is absent) [unclear why].
- So the two artifacts used different return joins.
- An implementer must define forward return explicitly: price on 31 Mar of year t+k over price on 31 Mar of year t, minus 1, using a continuous price series, so that missing fundamentals years do not delete returns.
- Total-return vs price-return: [not specified]. AoB p.9 formula: Return% = (P_final − P_initial)/P_initial.

### 1.2 Phase II panel (NB-1272, NB-1259)
- Universe of "2000 Sec". Scored N per test year is 1,100–1,500.
- "Features (Regime features) 2007–2025" (NB-1259).
- 14 test years. 2011 and 2018 are named as weak years, so 2011 is a test year. That implies test years 2011–2024 [inferred, not stated].
- Whether the 2016–17 gap exists in this panel: [not specified]. The 14 consecutive test years suggest it does not.
- The source/provider of the 2,000-security panel: [not specified].

### 1.3 Daily price panel (NB-1270)
- "n ≈ 1500, 252 days (years 2015–2025)".
- T/N = 1.68 against a "reliable threshold" of 2, with 2–5 "ideal" and ">5 deterministic". "Features don't increase observations."

### 1.4 BL-layer data inputs (BL §3)
- MarketAux: headlines, ticker tags, sentiment −1…+1, refreshed 3× daily.
- Screener/Finology scraper: 5-yr P&L, balance sheet, cash flow, share counts, refreshed each results season.
- Price feed: daily closes, market caps, Σ (daily/monthly).

### 1.5 Cleaning rules (RES p.48–51, BL §4.2)
These are labelled **"Recommended Cleaning"**. Evidence that they were applied to the WFO runs is [not specified].

1. **Nullify first-year values.** "Set growth to NaN when previous year was NaN." This applies to any gap, not only the first listed year.
2. **Cap extreme values.** "Replace values beyond P5/P95 with median." Whether P5/P95 and the median are per-year cross-sectional or pooled: [unclear].
3. **Flag duplicated values.** Identical extreme values in consecutive years are treated as Bloomberg extraction issues:

   | Ticker | Value | Years |
   |---|---|---|
   | PATANJAL | 164,295% | 2015, 2018 |
   | LLOYDSME | 28,500% | 2023–2025 |
   | TCOM | 15,880% | 2018–19 |
   | SWANCORP | 7,400% | 2020–21 |
   | FORH | 2,434% | 2019–20 |

   The action after flagging (drop or NaN) is [not specified].
4. Post-cleaning statistics:
   - Revenue growth: mean 2,127% → 14.0%, max 11.8M% → 60%.
   - EPS growth: mean 114% → 16.6%, max 164K% → 183%.
5. Pre-cleaning statistics:
   - Revenue growth: 5,680 obs, median 12.88%, P99 173.13%.
   - EPS growth: 5,499 obs, median 13.51%, P99 437.51%.
   - Outliers > 500%: 23 revenue (0.4%), 131 EPS (2.4%), 154 combined (1.4%).
6. BL composite (BL §4.2): winsorise each metric at P1/P99, then sector z-score, then flip lower-is-better signs.

### 1.6 Industry grouping
- 32 BSE industry groups (DM).
- The group names in the per-industry pages are fuller, e.g. "Agricultural, Commercial & Construction Vehicles", "Telecom - Services", "Pharmaceuticals & Biotechnology".
- **Household Products has 434 obs (39–54 names/yr)**, an order of magnitude more than any other group. It may be a catch-all/default bucket [unclear]; check on the new lake.
- Per-year industry Spearman is reported only when N ≥ 5 (all N ≤ 4 rows are N/A).

---

## 2. Feature dictionary

### 2.1 WFO feature set (RES p.5–6, p.9–18): 23 candidates

| Feature (exact source name) | Definition | Category | Sign constraint |
|---|---|---|---|
| Normalized ROE | raw field | Quality | + |
| Return on Assets | raw | Quality | + |
| Return on Invested Capital | raw | Quality | + |
| Operating Margin | raw | Quality | + |
| Revenue Growth Year over Year | raw | Growth | + |
| EPS - 1 Yr Growth | raw | Growth | + |
| Price to Sales Ratio | raw | Valuation | − |
| Price Earnings Ratio (P/E) | raw | Valuation | − |
| Periodic EV to Trailing 12M EBITDA | raw | Valuation | − |
| Price to Free Cash Flow | raw | Valuation | − |
| Net Debt/EBITDA | raw | Risk | − |
| Trailing 12M Free Cash Flow Yield | raw | **"unknown"** | **none** |
| EPS_Growth_3Y_Avg | 3-yr average of EPS growth | Growth | + |
| EPS_Growth_5Y_Avg | 5-yr average of EPS growth | Growth | + |
| Revenue_Growth_3Y_Avg | 3-yr average of revenue growth | Growth | + |
| Revenue_Growth_5Y_Avg | 5-yr average of revenue growth | Growth | + |
| ROE_3Y_Avg | 3-yr average of ROE | Quality | + |
| ROIC_3Y_Avg | 3-yr average of ROIC | Quality | + |
| **ROE_5Y_Avg** | 5-yr average of ROE [inferred] | **"unknown"** | none |
| **ROIC_5Y_Avg** | 5-yr average of ROIC [inferred] | **"unknown"** | none |
| Operating_Margin_Vol_3Y | 3-yr std of operating margin | Risk | − |
| ROE_Vol_3Y | 3-yr std of ROE | Risk | − |
| ROIC_Vol_3Y | 3-yr std of ROIC | Risk | − |

- ROE_5Y_Avg and ROIC_5Y_Avg are absent from the RES p.6 derived table but are used in every WFO window and ranked in the metric reports. The dossier omits them and says FCF Yield is the only sign-free feature.
- "ROE" in the ROE_*Y features is presumably Normalized ROE [unclear].
- Whether 3Y/5Y windows include the current year, and whether they span the 2016–17 gap: [not specified]. EPS_Growth_5Y_Avg first appears in 2014 and ROE_3Y_Avg in 2012, which implies trailing windows that include t (t−4…t and t−2…t, i.e. 5 and 3 rows).
- "Family" grouping for best-variant selection (RES p.11, 16, 18): ROIC / ROIC_3Y_Avg / ROIC_5Y_Avg form one family, and ROE_3Y / ROE_5Y another. Full family map: [not specified].

### 2.2 Industry-study feature names (DM p.12–48), which differ from the WFO set
- Additional or renamed features:
  - **Return on Common Equity** (ROE)
  - **Normalized Return on Assets**
  - **Earnings_Volatility_3yr** (the definition does not say whether it is EPS or EBIT [unclear])
  - **EPS_3yr_Avg** and **Revenue_3yr_Avg** (probably 3-yr average growth, but the name allows "average level" [unclear])
  - Normalized ROE
- The DM "Type" column labels **all growth metrics and FCF Yield as "Quality"**. Only valuation and risk are separate.
- The DM selection rules list risk as "Net Debt/EBITDA, Earnings Volatility".

### 2.3 BL-layer composite inputs (BL §4.1)
| Metric | Definition | Good direction | Refresh |
|---|---|---|---|
| P/E | Price ÷ TTM EPS | Lower | Daily |
| EV/EBITDA | EV = Mcap + net debt | Lower | Daily |
| P/B | | Lower | Daily |
| ROE | | Higher | Quarterly |
| ROIC | PAT ÷ capital employed | Higher | Quarterly |
| Piotroski-F | "not calc currently" | Higher | Quarterly |
| Revenue CAGR (5y) | | Higher | Quarterly |
| EPS CAGR (5y) | | Higher | Quarterly |
| Earnings volatility | std of EBIT over 5y | Lower | Quarterly |
| Net Debt/EBITDA | | Lower | Quarterly |

### 2.4 Phase II features
- **The "core features" fed to the RF are never enumerated** [not specified]. rf_config maps "Dates → Sectors → features" (NB-1267).
- Experimental "new" features ("titan features", NB-1262): "(3) Volatility (Momentum) + Market Regime + Market Cap" (NB-1259); "regime shift, market cap & volatility features" (NB-1272).
  - Exact definitions (windows, regime indicator, size measure): **[not specified]**.
  - Notebook verdict: "must move in together; whole is only thing, separately mean nothing" (NB-1259). "brought noise rather performance. Hence, they weren't selected" (NB-1272). "as per critical year comparison, adding the new features don't bring any statistical improvement… so we won't go with them further" (NB-1263).
  - However, NB-1272 also says "there's clear pattern to take advantage of → Raw much better than After features (correlation, long-short spread)".
- Breadth/divergence evidence motivating regime features (NB-1260):

  | Securities | Return | NIFTY | Notes |
  |---|---|---|---|
  | 35% | −12.62% | +13% | labelled "(+ve) securities" [unclear] |
  | 29.1% | −32.97% | +16% | labelled "(−ve) securities" |
  | 53% | +4% median return | +33% | |

  The window dates are [not specified]. The notebook flags "Median of whom?". The dossier drops the +ve/−ve labels.
- Live-layer features (NB-1274, NB-1279): momentum (relative strength, relative momentum); price-volume breakouts over 1-yr, 2-yr and multi-year (3–4 yr) ranges; mean return, past variance, autocorrelation.
- Macro layer: Indian 10Y yield and 1Y T-bill, "mixed with regime features" (NB-1268).

### 2.5 Univariate evidence (RES p.33 and p.46): average yearly Spearman

1-yr forward returns:

| Metric | Avg | | Metric | Avg |
|---|---|---|---|---|
| FCF Yield | +0.109 | | ROE_3Y_Avg | +0.044 |
| ROA | +0.092 | | ROIC_3Y_Avg | +0.036 |
| Normalized ROE | +0.086 | | ROIC_Vol_3Y | +0.006 |
| EPS_Growth_3Y_Avg | +0.083 | | ROE_5Y_Avg | −0.010 |
| EPS 1Y Growth | +0.076 | | Rev_Growth_5Y | −0.013 |
| EPS_Growth_5Y_Avg | +0.069 | | ROIC_5Y_Avg | −0.017 |
| ROIC | +0.067 | | RevGrowth YoY | −0.026 |
| | | | Rev_Growth_3Y | −0.026 |
| | | | ROE_Vol_3Y | −0.030 |
| | | | Op Margin | −0.043 |
| | | | P/E | −0.051 |
| | | | NetDebt/EBITDA | −0.053 |
| | | | P/S | −0.054 |
| | | | P/FCF | −0.064 |
| | | | EV/EBITDA | −0.087 |
| | | | OpMargin_Vol_3Y | −0.108 |

2-yr forward returns:
- Most negative: OpMargin_Vol_3Y −0.141, EV/EBITDA −0.117, P/S −0.090, P/FCF −0.083, Op Margin −0.082.
- Most positive: ROA +0.094, FCF Yield +0.089, EPS_Growth_3Y +0.089.

Other details:
- "Q1/Q5 Corr" = Spearman *within* the top/bottom 20% **by metric value**. For valuation ratios, Q1 is therefore the most expensive bucket.
- Operating Margin has a positive prior but a negative realised average.

---

## 3. Phase I linear models

### 3.1 Industry equations (DM p.12)
- Method: Spearman throughout. Target "future stock returns (1–5yr)". Equation fitted and scored against **3-yr returns**.
- Selection rules:
  - Quality/Growth must have positive Spearman.
  - Valuation (P/E, EV/EBITDA, P/S, P/FCF) must have negative Spearman.
  - Risk (Net Debt/EBITDA, Earnings Volatility) "can have" negative Spearman.
- Keep the top **up to 4** metrics by |ρ|. Petroleum Products has only 3.
- Weight: w_k = ρ_k / Σ_j|ρ_j|. Verified: Realty 0.6190/1.8791 = 0.329; Aerospace 0.4245/1.4369 = 0.295.
- Score = Σ w_k · Z(X_k). Whether Z is within industry-year or pooled across years: [unclear].
- Correlations are in-sample (same observations).
- Exact four-metric weights for every industry are in DM p.4–8 (the dossier §3.3 reproduces them correctly to 2 dp; 3-dp values are in DM p.12–48).

### 3.2 Unified (MR p.6)
`Score = −1.0·z(EV/EBITDA) − 1.0·z(P/S) + 1.0·z(FCF Yield)`
- z uses **training-period mean/std**, not within-industry.
- Source script: `create_comparison_table.py` ("fresh calculation").

### 3.3 Composite, legacy (MR p.6–7)
`Score = 0.3·(−Value) + 0.3·Quality + 0.3·Growth + 0.1·(−Risk)`
- Value = 0.40 P/E + 0.30 EV/EBITDA + 0.30 P/S
- Quality = 0.40 ROIC + 0.30 ROE + 0.30 ROA
- Growth = 0.50 EPS_Growth_3Y_Avg + 0.50 Revenue_Growth_3Y_Avg
- Risk = 1.00 Net Debt/EBITDA
- Composite_1Y vs Composite_3Y: presumably 1-yr vs 3-yr forward-return evaluation [unclear]. Script `wfo/test_composite_comparison.py`.
- Standardisation of the components: [not specified].

### 3.4 WFO constrained Ridge (RES p.8)
- Rolling 3-yr train, 1-yr test.
- Target: **1-yr forward return**.
- Constraints: Quality/Growth β ≥ 0; Valuation/Risk β ≤ 0; "unknown" unconstrained.
- Feature selection: p < 0.05 **and** VIF < 5, plus "better variant in same family selected". Examples: Return on Assets VIF 5.1 and ROIC VIF 5.5 were rejected.
- The p-value test is presumably univariate (correlation t-test) [unclear].
- Sign is enforced on coefficients, not at selection. For example, ROIC_Vol_3Y had corr +0.1785, was selected under a "negative" constraint, and received coefficient −2.66.
- Several selected features end with β = 0:
  - Window 8: 5 selected → 2 non-zero.
  - Window 9: 8 selected → 3 non-zero.
  - Window 10: 9 selected → 3 non-zero.
- **Ridge γ: [not specified].**
- No intercept in the published equations. Coefficients are on unstated scaling (e.g. −8.4235·P/S) [unclear whether z-scored].

### 3.5 Final_WFO (MR p.7)
- Equation (2024 test): `−0.32 z(EV/EBITDA) − 0.28 z(P/S) + 0.15 z(FCF Yield)`.
- "Training: Rolling window **or expanding** window" [unclear].
- Source: `wfo_final/test_results.csv`. IC: 2023 0.117, 2024 0.220.

### 3.6 PostCOVID (MR p.7)
- `−4.38 z(P/S) − 4.87 z(EV/EBITDA) − 3.24 z(P/FCF) + 8.64 z(FCF Yield)`
- "Optimized specifically on 2020–2023 data". 2024 IC 0.172.
- Source: `wfo_postcovid/test_results.csv`.

### 3.7 WFO_2Y / WFO_3Y (MR p.3)
- Sources: `wfo/wfo_results_2yr.csv` and `wfo/wfo_results.csv` (test_spearman column).
- WFO_2Y: 8 test years. WFO_3Y: 6 test years.

### 3.8 Banking APT template (REG). Illustrative only.
- Model: Rᵢ,t = α + Σβₖ Xₖ,t−1 + ε, quarterly.
- Variables (expected sign): ROE (+, TTM NI/avg equity); B/M (+, equity/mcap); ΔCredit cost (−, QoQ Δ in LLP/avg loans); CIR (−); YoY loan growth (+); CAR (+); Δ10Y G-sec yield (+, QoQ); YoY real GDP growth (+).
- Illustrative coefficients: α −0.045; ROE 0.85; B/M 0.52; ΔCC −1.20; CIR −0.25; Loan growth 0.40; CAR 0.30; Δ10Y 0.60; GDP 0.75.
- Illustrative fit: R² 0.68 / adj 0.66; F 45.8; n = 184; VIF max 1.77.
- Breusch-Pagan test, then White robust SEs. Collinearity thresholds: |r| > 0.7–0.8; VIF > 5 (or 10).
- Header: "a refined LLM output… yet to be structured by Jayesh". **Do not implement as a result.**

---

## 4. Phase II non-linear models

### 4.1 Bake-off (NB-1259)
| Learner | Notebook verdict |
|---|---|
| Random Forest | "Decent performance". NB-1272: "performs the best across all other models" |
| Poly Ridge | "High: Global against RF". "low Hybrid / low Industry against RF" |
| Linear Ridge | "(x) low performance" |
| LightGBM | "(x) low performance" |
| XGBoost | "Bad performance" |
| MLP | "(x)" with "low performance" |
| SVR | "Performs badly" |

- **Hyperparameters for every learner: [not specified]**. This includes RF n_estimators, max_depth, min_samples_leaf, max_features and seeds, and Poly Ridge degree and α.
- Report sections planned: "Random Forest vs. PolyRidge (why RF over polynomial)"; "Section 2: industry robustness analysis: is it industry standardization? → working of Global Standardization on Industry".

### 4.2 Specifications
- Raw → Global / Industry / Hybrid (NB-1261 diagram).
- "Global" = features standardised across the universe [inferred from the label].
- "Industry" = standardised within industry.
- **"Hybrid" is undefined.** The notebook itself demands "Specify what is hybrid approach there" (NB-1264) and "Specify Hybrid in Stability" (NB-1259).

### 4.3 Target, windows, protocol
- "WFO (3y training / 1y testing) ≈ 0.223" (NB-1272).
- Forward-return horizon of the label: [not specified]; 1-yr is implied by the 1-yr test.
- 14 test years.
- Rolling vs expanding: NB-1259 instructs to "update the yearly model performance wrt 3y WFO methodology rather expanding windows except for unified equation. What does full history fit mean?". NB-1261 says "Remove Section 12 → expanding window vs rolling 3y WFO". So some earlier results used expanding/full-history fits [unclear which].
- Production: rf_prediction_generator is a "weekly prediction job" (NB-1267). The historical_rf_generator clips predictions to [−80%, +120%] (NB-1286).

### 4.4 RF lineage / IC chain
- NB-1262: 0.16 (low N) → 0.182 (lower N) → 0.223 (Raw) → 0.233 (after features + 90/10) → **0.25 (+90/10, before features)**.
- NB-1259 has 0.16 → {0.198, 0.182} → 0.23 → 0.25 ("clearly specify"). **0.198 appears only there** [unclear].
- NB-1261 diagram: Raw → Global → {Feature → 90/10; before feature → 90/10}, with "(0.25) chosen" drawn near the upper (Feature) branch. This conflicts with NB-1262, which puts 0.25 on "before features". NB-1262 is the explicit version, so treat core + 90/10 = 0.25 as chosen.
- Raw RF: mean ≈ 0.223, **median 0.232**. "Couldn't perform up to the mark only for 2011 & 2018" (NB-1272).

### 4.5 The 90/10 objective: exactly what the sources say
- NB-1259: "Quant Model → 90/10 → Stability Factor (Variance b/w time across features)". An adjacent note reads "→ in Q5 but AR-Q1" [unclear].
- NB-1272: "90/10 → [90] expected Return; [10] Stability → Variance of feature". "0.22 to 0.25 or 0.23 → fruitful to take forward as it will increase the accuracy to 70% (positively performing)".
- NB-1260: "90/10 inconsistency". The pre-features baseline lacked 90/10 while the post-features run had it.
- NB-1261: "Feature impact summary (corrupted by 90/10 methodology)"; "Baseline features without 90/10 (Raw RF) / After features without 90/10 (Raw RF) → Inconsistency".
- **Not specified:**
  - whether 90/10 is a model-selection objective or a per-security scoring blend;
  - how "Performance"/"expected return" is measured (IC? predicted return?);
  - how "Stability" is computed (variance of which features, over which window, per security or per model);
  - normalisation/ranking before blending;
  - the sign of the variance term.
- Two readings are consistent with the text. Treat both as hypotheses to test:
  - (a) **Per-security score:** final = 0.9·rank(RF predicted return) + 0.1·rank(−temporal variance of the security's features).
  - (b) **Model-selection criterion:** 0.9·OOS IC + 0.1·stability across windows.
- The dossier's "Selection(m) = 0.9 Performance(m) + 0.1 Stability(m)" is reading (b). That is **[dossier-only]** formalisation.

### 4.6 Feature-impact chain (NB-1260)

| | Pre-features (low N) | Post-features + 90/10 | Change (as written) |
|---|---|---|---|
| Avg Spearman | 0.16 | 0.233 | 45% |
| +ve years ("correlation/LS?", the notebook is unsure which) | 11/14 | 14/14 | 21% |
| 2018 | −0.08 | 0.107 | ✓ |
| 2024 L/S | +12% | +22% | +90% (arithmetic gives +83%) |

- NB-1261: "Previously +ve years were 93% but now it shows ~100%, revisit & rectify". The glyph could also be read as "−100%".

### 4.7 Quantile checks (NB-1260; "Quantile Performance (Raw Model?)")

| Check | Pre-features | Post-features | Note |
|---|---|---|---|
| Q5 beats NIFTY | 7/10 | 11/17 | win rate 64.7% |
| D10 beats NIFTY | 7/10 | 11/17 | 64.7%; "117% (Alpha)" |
| L/S spread | Avg +15% | Avg +32% | |
| L/S positive | 12/14 | 16/17 | "Inconsistency" |

- Denominators of 10, 14 and 17 = "the discrepancy of inconsistent base". 17 > 14 test years is unexplained.
- In Phase II, Q5/D10/P95/P99 are the top buckets.

### 4.8 High-conviction and bottom-bucket tests (NB-1263)
- Median returns should rise D10 → P95 → P99 ("Done").
- Median returns should fall Q1 → D1 → P5 → P1 for bottom buckets (Section 7).
- Section 6 "granular win rate": 69%, 73%, 74%, 73% (NB-1272).
- Section 7 "Beating benchmark": 61%, 70%, 72%, 75%.
- Section 8: "win rates going down — good thing".
- Section 9: "Beating the benchmark (low probability)".
- Which four buckets the four numbers map to is [unclear]. Likely Q5, D10, P95, P99 in order.
- "Out of 14 only 2 times model wasn't able to beat the NIFTY benchmark" (NB-1272). **The years are not named.** The dossier's "(2011, 2018)" conflates this with the IC weak-years sentence.
- Part 4: "Probability Confusion Matrix: 35%, 55%, 85%, 15%" (NB-1272). Axes and definition: [unclear]. Omitted by the dossier.
- NB-1272 header: "70–75% → 90%" [unclear; likely a target].

### 4.9 Market-cap-conditioned checks (NB-1264)
- "Quantiles by Mkt Cap tested across the time-horizon."
- "Q5 as per recommendation, but as per testing either in Q1, Q2, Q3, Q4 across time (quantile analysis matrices across times)".
- "As the predicted Q5 security as per the Q5 per the market cap lying in the other quantile of the same market cap actual returns" — **count-based**, i.e. a transition matrix of predicted Q5 → realised quintile within the same cap bucket.
- Conclusion drawn from "avg / median / higher probable one… to base the confidence in model's ability".
- Cap-bucket definitions: [not specified].
- NB-1263 Section 15: "Mkt Cap Constraints, adding these only ruin the model's ability". This sits in the Phase II report outline, so it may refer to model-level cap conditioning, not only portfolio constraints [unclear]. NB-1285 separately records market-cap and sector caps "removed" from investment styles.

### 4.10 Win-rate definitions found across sources (inconsistent)
1. AoB p.10 Hit Ratio = % of Q1 stocks with positive return **or** beating benchmark.
2. AoB p.20 Win Rate = % of **months** with positive portfolio return (50–70% desirable).
3. BL §11 = "sign-hit-rate of views vs realised excess returns".
4. Phase I WFO `win_rate` column: **not** "% of Q1 positive". In 2022, Q1 averaged +161% but the win rate was 44.3%. Definition [unclear]; possibly % of correct pairwise or directional calls.
5. Phase II bucket win rates 69–74% vs "beats benchmark" 61–75%: separate metrics, definitions [not specified].

---

## 5. Platinum audit & §9 upgrades

- **Proposed (PLAT):**

  | Standard tool | Upgrade |
  |---|---|
  | SHAP | ALE |
  | PDP | Friedman H |
  | QRF | Conformal (MAPIE) + ACI "kill switch" |
  | t-SNE | TDA Mapper + Supervised UMAP (manifold on model error) |
  | MC Dropout | Adversarial Validation (high AUC = covariate shift; top feature = breakage source) |
  | K-fold / WFO | CPCV ("labels overlap (5-day returns)"; all paths; purge; embargo; distribution of Sharpes) |
  | Gini | Clustered Feature Importance |
  | (none) | Leaf depth / isolation score: "automate position size = 0 when anomaly score > threshold" |
  | Predicting returns | Causal Forests (EconML, CATE; "optimizes for policy") |

- **Audit walkthrough planned (NB-1264):** H-statistic (Friedman), Adversarial Validation ("testing & training set"), Out-of-Distribution.
- **Evidence of implementation:** none. No H values, AUCs, OOD thresholds, conformal coverage, CPCV paths, CFI clusters or causal-forest outputs exist in any source.
  - The thresholds (H materiality, AUC cut, anomaly cut) are **[not specified]**.
  - `shap_explainer.py` (SHAP) is in the alpha_model package (NB-1267), contrary to the SHAP → ALE upgrade.
- **[dossier-only]:** the H² formula and the isolation-forest s(x,n), c(n) formula are textbook additions, not source content.

---

## 6. Backtesting methodology (AoB)

### 6.1 Frameworks
- Walk-Forward (time-varying models), Monte Carlo/Bootstrapping (robustness/stress), Bayesian (posterior predictive).
- Chosen partly for "repute within the Industry".

### 6.2 WFO windows (anchors 31 March, 2010–2025)
- 3-yr: 13 windows (Mar-2010–Mar-2013 … Mar-2022–Mar-2025).
- 5-yr: 11 windows. 2-yr: 14 windows.
- "Proposed windows" (data collection):
  - 5-yr: 2016–21 … 2020–25 (5)
  - 2-yr: 2021–23, 2022–24, 2023–25
  - 3-yr: 2019–22 … 2022–25 (numbered 4–7)
- **Priority conflict:** p.7 lists 3y > 2y > 5y; p.25 lists 3y > 5y > 2y.
- The example text mentions a 5-yr IS / 1-yr OOS split for a 10-yr history.
- Hypothesis: a fundamental composite is low-frequency. Months are noise, 5+ yrs are stale.

### 6.3 Signal efficacy protocol
1. **Anchor T0:** score the whole universe available on 31 March using only data available then.
2. **Quantiles:** sort highest → lowest "+ Sector specification" (i.e. sector-relative), into quintiles or deciles. Q1 = top 20% ("Buy"), Q5 = bottom ("Avoid/Short"). This is the Phase I labelling.
3. **Forward look:** horizons **2, 3 and 5 yrs**. Delisted (bankruptcy) = −100% or liquidation value; acquired = cash-out value.
4. **Tests:**
   - Rank IC = Spearman(T0 score, T+N return), goal > 0.05.
   - L/S = mean(Q1 sector top tier) − mean(Q5).
   - Hit ratio (see §4.10).
5. **Success criteria:** monotonicity, spread, persistence.
6. **Decision matrix:** the 3-yr spread should be highest. 5-yr ≪ 3-yr means "decay"; 2-yr random with 3-yr high means "noise / slow pricing".

### 6.4 Stitching
- Roll the portfolio at each anchor: sell the old cohort, buy the new top-ranked cohort. Example: 15 highest-score stocks, held 3 yrs, e.g. Mar-2016 → Mar-2019.
- Link OOS segments into one equity curve.

### 6.5 Parameters learned per WFO fold
- λ (EWMA decay of technical data), κ (score → return conversion), w (technical vs fundamental weight).
- Example values: λ 0.97, κ 120 bps, w 0.6.
- MC-stable ranges: λ 0.975 avg [0.97–0.985]; **κ "120 bps per month"** [80–150]; w 0.55 [0.45–0.65].

### 6.6 Monte Carlo / bootstrap
Four de-path-dependence methods:
- (a) resample/permute WFO return order, with or without replacement;
- (b) randomise window boundaries, lengths and cycles;
- (c) perturb parameters within confidence intervals;
- (d) simulate from fitted GARCH, regime-switching or block-bootstrap models.

Execution: **1,000 scenarios**; re-run the already-trained strategy; record Sharpe, MDD, CAGR.

Outputs:
- median Sharpe/return;
- 5th–95th percentile band;
- probability of ruin, defined as **drawdown > 20%**;
- stability of λ, κ, w.

Illustrative results: Sharpe 2.1 (WFO) / 2.0 (MC median) / 1.6 (MC p5); MDD 11.9% / 12% / 18%; P(DD > 20%) 6%.

### 6.7 Clearance thresholds and metrics
- Clearance: Sharpe > 2, DD < 15%, turnover ≤ 25%, "across the lower-tail MC results".
- Metric table (p.20–21):

  | Metric | Threshold / reading |
  |---|---|
  | Sharpe | > 2.0 |
  | MDD | < 15% |
  | CAGR | beat market |
  | Sortino | > 2.0 |
  | Calmar | > 2.0 |
  | Win rate (% months positive) | 50–70% |
  | Information Ratio | higher is better |
  | Turnover | ≤ 25%/yr |
  | Alpha | > 0 |
  | Beta | regime analysis |

### 6.8 Synthetic-portfolio harness (NB-1286)
1. Synthetic portfolio JSON (StockID, Weight, Style). Strategy-based and persona-based portfolios.
2. Load: read JSON → internal representation → extract metadata.
3. Dict(UUID → float): holdings sum to 1, no short selling.
4. Backtesting engine: RF predictions → optimisation → execute trades → period returns.
5. Historical RF generator: fetch historical features → load appropriate model ("wf optimization – RF + CAPM") → predict → clip to [−80%, +120%].
6. RF predictions per stock.
7. BL optimisation.
8. Mean-variance optimisation.
9. Trade execution simulation: min trade size 2%; cost **0.1% per trade**. Note this differs from the 12 bps in config.
10. Performance metrics.

- "How composite score is calculated?" → 0.4 data completeness + 0.3 model confidence + 0.2 feature stability + 0.1 prediction magnitude. Component definitions: [not specified].
- Also listed: quarterly features, reverse-engineering the test portfolios, macro layer.
- Backtest engine integrations (NB-1268): Nudges, **Protection Period** (undefined), RF Penalty.

---

## 7. Black-Litterman & portfolio

### 7.1 Prior
- BL spec: π = "market-implied (equilibrium) return implied by market-cap weights".
- Implementation (NB-1268, 1283): CAPM. π = Rf + β(Rm − Rf).
  - Rf = 7% (Indian 1Y T-bill), "dynamic – not big/asymmetry".
  - Rm = 12% "(loss aversion)", intended to become "sector mean → dynamic".
  - β "market wide / sector specific". Rm alternatively "Sector/Mkt return".
  - The 10Y bond yield goes to the macro layer.
- β = Cov(security, market)/Var(market) over **252 trading days, min 60**, clip **[−1, 3.0]**, fallback ≈ 1. Notes: "252 days (expand it) / different profiles"; "Beta 252/60 should be on similar line → time period (5-yr tenure data points too stretched)".

### 7.2 Views
- **Implementation:** Q = RF model return ("the prediction number", NB-1282). P = identity rows, one view per security ("views are with respect to security ID").
- **BL v1.0:** Q_stock = 0.02 × S_i,t (S ∈ [−1, 1], so +1 ⇒ +2% excess).
  - S_i,t = (1−α)S_new + αS_{t−1}, α = e^(−ln2/60) (half-life ≈ 60 days; CI tracks {45, 60, 75} d).
  - Pillars (BL §4.3–4.4):
    - S_value = mean(z_P/E, z_EV/EBITDA, z_P/B)
    - S_quality = mean(z_ROE, z_ROIC, z_Piotroski)
    - S_growth = mean(z_RevCAGR5, z_EPSCAGR5)
    - S_risk = mean(z_EarnVol, z_ND/EBITDA), sign-flipped
    - Sᵢ = 0.30V + 0.30Q + 0.30G + 0.10R, **clipped to [−1, 1]**
  - Sector views Q_sector plus a residual stock view ε ("portion of Q_stock remaining after regressing out the sector view", "see §5.4"). **§5 is missing from the document.**
  - Ω_sector inflates if a sector gets < 3 headlines. Residualisation on/off toggle.
- Nudge rationale example: "Tech sentiment +1.4σ; 27% below sector P/E".

### 7.3 Ω (three versions)
- (i) BL §4.6: Ω = τ·diag(PΣPᵀ) ("historical variance of Sᵢ ideally, but needs 10 years of data").
- (ii) NB-1269/1270: Ω = τ × variance(security), "dynamic", scaled by a "confidence-based uncertainty matrix" whose proxy is **data completeness** (NB-1268). Functional form [not specified].
- (iii) **NB-1282/1283: Ω = 3 × Pᵢ(τΣ)Pᵢ′ "(He–Litterman)"**.
  - With the factor 3, a single uncorrelated view gets weight τσ²/(τσ² + 3τσ²) = **0.25**, not the dossier's 0.5 at "Ω = τσ²".
- NB-1281 nonetheless says "Omega is low so RF dominates (95% weight)". This is unreconciled.
- Low Ω → toward alpha/RF; high Ω (bad/short data) → toward CAPM.

### 7.4 τ
- Fixed 0.05 (config also lists 0.025).
- A/B against adaptive τ = max(0.05, 15/data-days), and "adaptive τ = 1/T (no. of observations)". The two performed equally well.
- Notebook: "not affecting significantly as omega is the determining factor"; "Dependent upon Omega's value (revisit)".
- Small τ = trust CAPM; large τ = trust alpha.

### 7.5 Posterior (NB-1282, BL §6)
- E(R) = [(τΣ)⁻¹ + P′Ω⁻¹P]⁻¹ [(τΣ)⁻¹π + P′Ω⁻¹Q]
- Var(R) = [(τΣ)⁻¹ + P′Ω⁻¹P]⁻¹, marked "Correct". The variant "(Σ/T + Ω)" is marked "Incorrect".
- Woodbury form (BL): μ = π + τΣPᵀ(PτΣPᵀ + Ω)⁻¹(Q − Pπ).
- w* = (1/λ)Σ⁻¹μ_BL.

### 7.6 Covariance
- "Compute Cov Matrix (Ledoit-Wolf + OAS + L2)" (NB-1283).
- LW: δ·target + (1−δ)·sample, target "assumes no correlation", optimal intensity.
- OAS vs LW chosen by "hit and trial with selection of one with stable matrix".
- Ridge L2 adds a small positive value to the diagonal:
  - BL spec +1e−6;
  - implemented **1e−5** "lower than default after A/B";
  - NB-1285 mentions "Dynamic L2 regularization calculation" [unclear].
- Inspect eigenvalues (largest → smallest = "how much it stretches") and eigenvectors (direction).
- "Sample 30 securities with 250 data points – covariance will be unreliable."

### 7.7 Optimiser
- Objective: max(return − λ·risk²) + profile-dependent trade limit (NB-1283). Mean-variance; constraints are hard boundaries (NB-1284).
- Portfolio optimiser objectives: max Sharpe / min volatility / target return.
- Efficient frontier: 50 points ("industry std").
- Covariance, transaction cost and utility: utility function (return, risk, transaction cost) (NB-1271, 1276).

### 7.8 Constraints / styles (NB-1269, 1285)

| Style | Stop-loss | Large-cap | Mid-cap | Small-cap |
|---|---|---|---|---|
| Conservative | 20% | 45–73% | 15–30% | 2–15% |
| Balanced | 25% | 30–60% | 20–35% | 5–25% |
| Growth | 30% | 15–52% | 25–40% | 8–35% |
| Aggressive | 40% | 5–45% | 30–50% | 10–50% |

- Market-cap and sector caps were later **removed** (NB-1285). Security cap / max stake 5–25% remains. Sector concentration limit is "style dependent" (pre-removal).
- Risk-aversion options (NB-1284): fixed → dynamic (calculated from portfolios). Choose among "what investor's portfolio tells", "what investor tells", or "avg (truth – middle ground)", via a utility-max function.
  - Mapping: Conservative = high risk aversion; Balanced = average; Aggressive = low (NB-1276).
  - Reason: "When we aren't applying sector & market-cap constraints, the line b/w diff risk profiles fade, hence dynamic risk aversion" (NB-1284).

### 7.9 Global parameters
- Transaction cost 12 bps (tax and opportunity cost not included).
- Turnover cap 20% (config); BL spec 25%/yr. Per rebalance: "3 or 25%" (security or portfolio level). Per year: "unspecified".
- NB-1280: "constraints on n(trades) – yearly & turnover – 25%".
- Risk aversion 2.5 (cap) / 1.5 used. Micro-cap hard floor ₹500 Cr.
- Turnover rule (BL §8): estimate annualised turnover; if > 25%, scale or drop trades; cancel trades whose alpha < costs. Turnover = (buys + sells)/portfolio size, annualised.
- Rebalance test (NB-1279): actual alpha = (Δ expected return) × (new − old weights). Must clear opportunity cost + tax.

### 7.10 RF penalty
- "RF < 18% → consider negative expected return" (NB-1271).
- Item 2 of NB-1284: "Solve for if less than 18% → negative return → false assumption".

### 7.11 Nudges (BL §7)
| Δw | Action |
|---|---|
| ≥ +100% | Load up |
| +25…100% | Add a bit |
| −25…+25% | Hold |
| −100…−25% | Trim / book profit |
| ≤ −100% | Exit |

### 7.12 Diversification recommender (NB-1284)
- RRG: RS-Ratio = sector index / benchmark; RS-Momentum = "rate of Δ (RS-Ratio, 60 weeks)". The dossier's ×100 scaling is [dossier-only].
- Trigger: any sector with exposure < 5%.
- Score = Performance 30% (Sharpe) + Diversification 25% ("correlation with existing sectors") + RRG momentum 25% + Valuation 20% (P/E across time horizons).
- Penalty based on economic cycle. Rank 0 → 1. Recommend top 3 survivors. Each sector is represented by its 2 top names by market cap. "Construct index = (R…" [unclear].
- Other analytics: performance_charts uses the **Modified Dietz** method and a 5-point chart; fundamental_analyzer checks portfolio quality via PE, PB, ROE.

### 7.13 Live management (NB-1274, 1277–1279)

Security / sector / NIFTY decision matrix:

| Security | Sector | NIFTY | Action |
|---|---|---|---|
| ↑ | ↑ | ↑ | Hold even past expected return; no adds |
| ↓ | ↑ | ↑ | Sell/trim; verify against dynamic and quarterly fundamentals |
| ↑ | ↓ | ↑ | Hold (strongest among peers) |
| ↑ | ↓ | ↓ | Hold (flight to safety) |
| ↓ | ↓ | ↑ | Outperforms sector: hold. Underperforms: hold (Aggressive) or trim (Conservative) |
| ↓ | ↑ | ↓ | Re-rate / downgrade (fundamentals problem) |
| ↓ | ↓ | ↓ | Outperform both: hold/flight to safety. Underperform both: trim/exit, depending on alpha, profile and fundamentals |

Other rules:
- Weekly re-rate after a holding exceeds 50% of expected return.
- "(+) Bi-monthly" [unclear].
- Tenure ≈ 6 months (intention long-term).
- Wait ~7 days for noise after big news.
- Price-volume breakouts over yearly, 2-yr and 3–4-yr ranges: positive breakout = Buy, negative = Sell, scaled by profile.
- Max drawdown and stop-loss are measured from the average purchase price.
- "60% of features are dynamic… true?" is a question in the notes, not an established fact.
- News sentiment: 3× daily.
- Batch schedule: ETL 08:00/12:00/16:00 (5 min max), views 17:00 (3 min), BL + nudges 17:05 (2 min).
- Alerts: latency > 10 min; turnover > 25%. Monthly IC & Sharpe-uplift report.
- CI: nightly replay Jan-2022 → T−1 with frozen Σ and π. Logs IR, realised vol, MDD, turnover, sign-hit-rate. A parameter set is champion if IR ↑ and turnover ≤ cap for ≥ 6 consecutive months. Red metrics block deployment.
- Evaluation formulas (NB-1275–1276):
  - Sharpe (assumes σ constant; compare "SR holding period vs SR present"; "does EWMA help");
  - Sortino;
  - Treynor (diversified when β_p ≈ 1);
  - IR = (Rp − Rm)/σ(Rp − Rb);
  - M² (match market risk);
  - MWROR vs TWROR;
  - RAP = Active Return/(2 × Active Risk²) [unclear "2"/"λ"].

---

## 8. Results tables worth reproducing (reference only; different dataset)

### 8.1 Pooled correlations (DM p.2)
| Horizon | Pearson | Spearman |
|---|---|---|
| 1-yr | −0.01500 | +0.01920 |
| 2-yr | −0.01597 | +0.04159 |
| 3-yr | −0.01594 | +0.06932 |
| 5-yr | −0.01851 | +0.05947 |

Which composite this is: [unclear] (page headed "Fundamental Composite Eq").

### 8.2 Seven-model table (MR p.2–3)
- Values are as the dossier §3.5 reports (verified identical).
- Positive years:

  | Model | Positive years |
  |---|---|
  | Composite_3Y | 8/12 |
  | Unified | 10/12 |
  | Composite_1Y | 8/12 |
  | WFO_2Y | 6/8 |
  | WFO_3Y | 4/6 |
  | Final_WFO | 2/2 |
  | PostCOVID | 1/1 |

- Bold in source = |IC| > 0.15.
- All 9 Composite_3Y corrections:

  | Year | Before | After |
  |---|---|---|
  | 2011 | 0.289 | 0.259 |
  | 2012 | 0.321 | 0.286 |
  | 2013 | 0.300 | 0.319 |
  | 2014 | 0.185 | 0.188 |
  | 2018 | −0.150 | −0.143 |
  | 2019 | −0.029 | −0.024 |
  | 2021 | 0.184 | 0.204 |
  | 2022 | −0.037 | −0.053 |
  | 2023 | −0.093 | −0.089 |

### 8.3 WFO quintile run (RES p.2)

| Win | Train | Test | n_tr | n_te | train_corr | test_corr (Pearson) | test_Sp | Q1 | Q5 | L/S | win |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2010–12 | 2013 | 222 | 260 | 0.312 | 0.153 | 0.214 | 49.525 | 24.256 | 25.269 | 0.600 |
| 2 | 2011–13 | 2014 | 482 | 277 | 0.266 | 0.165 | 0.252 | 106.898 | 55.008 | 51.890 | 0.596 |
| 4 | 2018–20 | 2021 | 882 | 319 | 0.056 | −0.089 | −0.031 | −0.854 | −18.0 | 17.146 | 0.442 |
| 5 | 2019–21 | 2022 | 935 | 345 | 0.018 | −0.067 | −0.149 | 161.17 | 191.715 | −30.545 | 0.443 |
| 6 | 2020–22 | 2023 | 995 | 360 | 0.148 | 0.009 | 0.101 | 49.792 | 39.135 | 10.657 | 0.544 |
| 7 | 2021–23 | 2024 | 1092 | 382 | 0.112 | 0.149 | 0.202 | 25.348 | −2.033 | 27.381 | 0.550 |

Window 3 (test 2015) is absent from this run.

### 8.4 Standalone constrained-Ridge WFO (RES p.8–19)

Summary:
- 5 windows reported, with IDs 3, 7, 8, 9, 10.
- The note says "3 window(s) excluded due to 2016–2017 data gap". Windows 1, 2 and 4–6 are absent, so **two absences are unexplained**.
- Avg train R² −0.1191; adj −0.1251; avg test corr −0.0162; avg test R² −0.5121; avg L/S 0.78%; win 48.0%; 3/5 windows with positive spread.

Per window:

| W | Train→Test | n_tr / n_te | Train R² / corr | Test Pearson / Spearman / R² | Q1 / Q5 / L/S / Win | Equation |
|---|---|---|---|---|---|---|
| 3 | 2012–14→2015 | 800/289 | −0.2400 / 0.1467 | −0.1763 / 0.0787 / −1.1851 | 19.68 / −1.81 / 21.49 / 54.3% | −8.4235 P/S −5.8810 EV/EBITDA −2.6594 ROIC_Vol_3Y |
| 7 | 2018–20→2021 | 1087/394 | −0.0927 / 0.0082 | 0.0077 / −0.3568 / −0.0292 | −14.41 / −8.40 / −6.02 / 33.0% | −0.5839 P/S |
| 8 | 2019–21→2022 | 924/339 | −0.0003 / 0.0289 | −0.0768 / −0.1913 / −0.7516 | 148.24 / 195.06 / −46.82 / 42.8% | −1.6313 FCFYield −1.8215 ND/EBITDA |
| 9 | 2020–22→2023 | 1071/374 | −0.0860 / 0.1366 | 0.0154 / 0.1052 / −0.4773 | 48.24 / 40.37 / 7.87 / 55.1% | −10.3849 P/S −5.5470 EV/EBITDA +9.8800 FCFYield |
| 10 | 2021–23→2024 | 1097/382 | −0.1763 / 0.1076 | 0.1488 / 0.2009 / −0.1172 | 25.35 / −2.03 / 27.38 / 55.0% | −6.1845 P/S −6.0614 EV/EBITDA +8.2568 FCFYield |

- In the RES p.19 aggregate table, the "Test Corr" column is **Pearson**.
- Window 7 selected ROIC (+ constraint) and P/S, but only P/S survived.

### 8.5 Phase II reference numbers
- IC chain: 0.16 / 0.182 / 0.223 (median 0.232) / 0.233 / 0.25.
- Positive years 11/14 → 14/14. 2018: −0.08 → 0.107. 2024 L/S: 12% → 22%.
- Bucket win rates 69/73/74/73%; benchmark-beat 61/70/72/75%; quantile table in §4.7.

---

## 9. Discrepancies & open items

1. **Ω factor.** NB-1282/1283 give 3·Pᵢ(τΣ)Pᵢ′. BL spec and dossier give τσ². The dossier's "half weight at Ω = τσ²" does not describe the implemented He–Litterman form (¼ weight). NB-1281's "95% weight to RF" fits neither without the unspecified data-completeness scaling. τ-cancellation in the posterior mean still holds.
2. **90/10.** The dossier formalises it as model selection, Selection(m) = 0.9·Perf + 0.1·Stab [dossier-only]. The sources only say "90 expected return / 10 stability = variance (b/w time) of features". Neither metric is defined. The notebook itself flags "90/10 inconsistency" and "corrupted by 90/10 methodology".
3. **"Every test year positive" for the chosen 0.25 model** [dossier-only]. 14/14 is recorded for the post-features + 90/10 (0.233) run (NB-1260). NB-1261 says positive years went from 93% to ~100% and must be revisited.
4. **"Fell short of NIFTY only in 2011, 2018"** [dossier conflation]. NB-1272 says the model "couldn't perform up to the mark only for 2011 & 2018" (IC context) and separately "out of 14 only 2 times wasn't able to beat NIFTY" (years unnamed).
5. **Chosen branch.** NB-1261's diagram puts "(0.25) chosen" by the feature branch; NB-1262 puts 0.25 on "before features". 0.198 (NB-1259) is unexplained.
6. **Quantile denominators** 10/14/17 ("inconsistent base"). "117% (Alpha)" is undefined. "+90%" vs computed +83%; "+45%" for 0.16 → 0.233 is correct (+45.6%); "21%" is the difference of 3/14.
7. **RF-hybrid** shows 0.21 in the legacy Part-1 summary vs 0.05 elsewhere (NB-1261). Recompute. "Hybrid" is undefined.
8. **Feature set.** The dossier omits ROE_5Y_Avg and ROIC_5Y_Avg (both "unknown" sign). It treats FCF Yield as Quality (DM) vs "unknown" (RES). Industry-study names (Return on Common Equity, Normalized ROA, Earnings_Volatility_3yr, EPS_3yr_Avg, Revenue_3yr_Avg) don't map 1:1 to the WFO names.
9. **Standardisation.** The dossier says "within-industry in Phase I". In fact Unified uses training-period mean/std (global); industry equations use within-industry Z; the BL spec uses sector z after P1/P99 winsorisation.
10. **Cleaning rule.** The dossier says "first year of data". The source says "when previous year was NaN" and labels the rules "Recommended". Whether they were applied, and whether the P5/P95 median is per-year, is unknown.
11. **Forward-return alignment** differs between DM (gap-year N/A) and RES (2015 returns present, 2010 metric-IC absent).
12. **Final_WFO vs standalone run.** 2024: 0.220 with (−0.32, −0.28, +0.15) vs 0.2009 with (−6.18, −6.06, +8.26). 2023: 0.117 vs 0.105. Different runs, despite the dossier presenting the standalone run as "a later run of the same protocol".
13. **WFO window bookkeeping.** Quintile run IDs 1, 2, 4–7 (no test 2015). Standalone IDs 3, 7–10. Training Ns differ (882 vs 1087 for 2018–20).
14. **Rolling vs expanding.** Final_WFO says "rolling or expanding". The notebook demands redoing results as 3y rolling except Unified and removing the expanding-vs-rolling section.
15. **Window priority conflict** (AoB p.7 vs p.25).
16. **Transaction cost.** 12 bps (config) vs 0.1%/trade (harness). Turnover cap 20% (config) vs 25% (spec, CI alerts, NB-1280).
17. **Composite definitions.** Three distinct composites exist: legacy MR composite; BL v1.0 Sᵢ (with P/B, Piotroski not computed, 5y CAGRs, EBIT vol, clip ±1); harness score 0.4/0.3/0.2/0.1. The dossier's "Composite: 0.3(−Val)+…" omits the sub-weights.
18. **BL §5 (sector/residual views)** is referenced but absent. The dossier omits sector views and ε.
19. **Author's IC → probability claim** (RES p.2: 0.1 → 55%, 0.3 → 65%). The dossier correctly re-derives it (arcsin). The sources contain no arcsin, 2.8·IC·σ or fundamental-law material [dossier-only].
20. **Other [dossier-only] content:**
    - H² and isolation formulas;
    - RRG ×100 scaling and the (1−ρ) transform;
    - the LW κ formula details;
    - the RF variance formula;
    - "Composite_3Y IC 0.253 (2010–14) → 0.023 (2018–24)" (derived averages, not stated);
    - "regime features let the models see the 2008 crisis" (inference).
21. **Platinum audit** has no recorded outputs. The thresholds are unspecified. SHAP is still in the codebase.
22. **Notebook questions, not facts:** "60% of features dynamic, true?"; "Omega ratio? (Greeks?)"; "We aren't doing it anymore" (pointing near scaling factor/T-bill [unclear]); "Protection Period" undefined; "Probability confusion matrix 35/55/85/15%" undefined; "→ in Q5 but AR-Q1" [unclear].
23. **CPCV rationale** cites "5-day returns" label overlap, which is irrelevant to annual labels. With annual rebalancing and 1-yr labels, purge/embargo would need a ≥ 1-yr gap.
24. **Market-cap constraint statement** (NB-1263 §15) may refer to modelling, not only portfolio styles.

---

## 10. Implementation checklist (testable specs)

1. **Panel.** Annual 31-March anchors. One row per (ticker, year). Point-in-time universe per anchor (no survivorship). Keep delisted names: return = −100% (or liquidation value); acquired = cash-out value.
2. **Forward return.** fwd_k(t) = P(31-Mar of t+k)/P(31-Mar of t) − 1 for k ∈ {1, 2, 3, 5}, from a continuous price series. Do **not** reproduce the 2016–17 gap. Test: no N/A forward returns caused by missing fundamentals years.
3. **Growth cleaning (as recommended).**
   - (a) Growth = NaN if the prior-year base value is NaN.
   - (b) Per year [choose and document; source unclear], replace values outside [P5, P95] with the median.
   - (c) Flag identical extreme growth values (> 500%) repeated in consecutive years for the same ticker.
   - Test against the reference shape: revenue-growth mean falls to the order of ~14%, max ~60%.
4. **Feature set.**
   - Implement all 23 WFO candidates (§2.1) with trailing 3Y/5Y averages and 3Y std over (t−2…t)/(t−4…t), requiring 3/5 non-missing values [decision].
   - Add the industry-study extras (ROE, Normalized ROA, Earnings_Volatility_3yr) as aliases if available.
   - Sign constraints: Quality/Growth +, Valuation/Risk −, FCF Yield / ROE_5Y / ROIC_5Y "unknown". Recommended fix: give FCF Yield prior + (the source flags its sign flip as a config gap).
5. **Univariate IC report.** Per feature and year: Spearman with fwd_1 and fwd_2, N, and within-Q1/Q5 Spearman (Q by metric value, 20%). Average across years. Rank as in RES p.33/46.
6. **Industry equations.**
   - Within each industry: z-score (industry-year [decision]).
   - Univariate Spearman vs fwd_3, then apply sign filters (valuation must be <0, quality/growth >0, risk ≤0).
   - Keep the top 4 by |ρ|; weights ρ/Σ|ρ|.
   - Report overall in-sample Spearman, and per-year Spearman only when N ≥ 5.
   - Label results "descriptive, in-sample".
7. **Unified score.** −z(EV/EBITDA) − z(P/S) + z(FCF Yield), z with training-window mean/std. Report the yearly Spearman vs fwd_1.
8. **Legacy composite.** 0.3(−V) + 0.3Q + 0.3G + 0.1(−R), with V = .4 P/E + .3 EV/EBITDA + .3 P/S; Q = .4 ROIC + .3 ROE + .3 ROA; G = .5 EPS_g3 + .5 Rev_g3; R = ND/EBITDA. Components z-scored [decision]. Evaluate vs fwd_1 (Composite_1Y) and fwd_3 (Composite_3Y).
9. **WFO constrained Ridge.**
   - Rolling 3-yr train / 1-yr test, target fwd_1.
   - Selection: p < 0.05 (univariate) and VIF < 5, best variant per family.
   - Ridge with bound constraints (β ≥ 0 or ≤ 0 by category). γ via inner CV [source silent; document it].
   - No intercept.
   - Output per window: n_train, n_test, train R²/adj R²/corr, test Pearson/Spearman/R², Q1/Q5 mean returns, L/S, win rate (define explicitly), equation.
   - Also run 2-yr and 5-yr windows.
   - Also run a no-COVID variant excluding test years 2021 and 2022.
10. **Bucket conventions.** Phase I: Q1 = top. Phase II: Q5/D10/P95/P99 = top. Pick one convention for the new code and map the reference tables.
11. **Efficacy tests (AoB).** For 2-, 3- and 5-yr horizons:
    - rank IC (goal > 0.05);
    - sector-relative quintile L/S;
    - hit ratio (% top-bucket positive; and % beating benchmark, reported separately);
    - monotonicity across quintiles;
    - decay/noise decision rule (3-yr spread highest is the hypothesis).
12. **Stitched OOS curve.** At each anchor, buy the top-N (N = 15 example) equal-weight [decision], hold to the next roll, and chain the returns.
13. **Phase II bake-off.**
    - Learners: RF, Poly Ridge, Linear Ridge, LightGBM, XGBoost, MLP, SVR.
    - Same features, 3-yr rolling train / 1-yr test, 14 test years.
    - Specifications: global z, industry z, hybrid (define it and document; source undefined).
    - Hyperparameters: source silent. Fix a documented grid; tune only inside training windows.
    - Acceptance reference: RF most robust across the three specifications; raw-RF mean IC ≈ 0.22 (median ≈ 0.23) on the ~2,000-name panel.
14. **90/10.** Implement as a configurable blend with both readings:
    - (a) per-security final score = 0.9·rank(pred) + 0.1·rank(−temporal variance of the security's standardized features over the training window);
    - (b) model-selection criterion = 0.9·mean OOS IC + 0.1·stability.
    - Report each against "no 90/10" so the "90/10 inconsistency" (unequal comparisons) is not repeated.
    - The exact stability formula is a documented decision, not a source fact.
15. **Feature-impact ablation.** Run a 2×2 grid {core, core + regime/mcap/vol} × {no 90/10, 90/10} on identical N and years. Report the mean, median and std of IC, positive years, 2018 IC, and 2024 L/S. Reference: 0.223 / 0.233 / 0.25.
    - The source never defines the regime, market-cap or volatility features, so define them explicitly (e.g. market breadth % above MA, log mcap, 12-1 momentum, trailing vol).
    - Test them jointly and separately ("must move together").
16. **Quantile diagnostics.**
    - Per year: Q5, D10, P95, P99 (and Q1, D1, P5, P1) median and mean returns.
    - Check monotonicity: top medians rise D10 → P95 → P99; bottom falls Q1 → D1 → P5 → P1.
    - Bucket win rate and % beating NIFTY.
    - Use a single denominator (number of test years) for every count.
17. **Market-cap-conditioned matrix.** Within each cap bucket, cross-tab predicted top-quintile vs realised quintile (counts, across years). Report the modal, mean and median realised quintile.
18. **Breadth/divergence check.** Per year: share of stocks with negative return, their mean, the median stock return vs NIFTY. Reference pattern: median +4% vs NIFTY +33%.
19. **Platinum audit (to implement; no reference values exist).**
    - Friedman H on top feature pairs.
    - Adversarial validation AUC per window, with the top shift feature.
    - Isolation/leaf-depth OOD score with position = 0 above a threshold (choose and document the threshold).
    - Optional upgrades: conformal intervals (MAPIE/ACI), CPCV with ≥ 1-yr purge/embargo, clustered importance, ALE instead of SHAP.
20. **BL engine.**
    - π_i = Rf + β_i(Rm − Rf), Rf = 7%, Rm = 12% (configurable; sector-mean option).
    - β from 252 daily returns, min 60, clip [−1, 3], fallback 1.
    - Q = RF predicted return, in the **same units as π** (total vs excess).
    - P = identity rows.
    - Ω_ii = c·τ·σ_i² with c ∈ {1 (spec), 3 (He–Litterman, notebook)} and an explicit data-completeness multiplier (define it).
    - τ = 0.05. Test: posterior mean invariant to τ when Ω ∝ τ.
    - Posterior via Woodbury. Var = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹.
21. **Covariance.** Ledoit-Wolf (constant-variance/zero-correlation target) and OAS; choose by condition number/eigenvalue stability. Add ridge 1e−5 to the diagonal. Log T/N (reference 1.68).
22. **Optimiser.**
    - Objective: max μᵀw − λ wᵀΣw; λ = 1.5 (cap 2.5), dynamic per profile.
    - Long-only; Σw = 1. Per-security cap 5–25% by style. Stop-loss 20/25/30/40%. Micro-cap floor ₹500 Cr. No cap/sector caps.
    - Per-rebalance limit: 3 trades or 25%. Turnover cap 20%/yr (spec 25%). Cost 12 bps.
    - Drop trades with Δμ·Δw < cost (+ tax + opportunity cost).
    - Efficient frontier: 50 points.
23. **RF penalty.** Expose as a config flag (threshold 18%). The notebook calls it a "false assumption", so default it off and test both.
24. **Nudge mapping.** Implement the thresholds in §7.11 on relative Δw.
25. **EWMA view smoothing (if a composite view is used).** α = e^(−ln2/h), h ∈ {45, 60, 75} days. Sᵢ clipped to [−1, 1]; Q = 0.02·Sᵢ.
26. **Monte Carlo.**
    - 1,000 paths: block bootstrap of OOS returns, randomised window boundaries, parameter perturbation.
    - Report median and p5/p95 Sharpe, MDD, CAGR, and P(DD > 20%).
    - Clearance: Sharpe > 2, MDD < 15%, turnover ≤ 25% at the lower tail. Also Sortino > 2, Calmar > 2, monthly win rate 50–70%, alpha > 0.
27. **Synthetic-portfolio harness.** Follow the 10 steps (§6.8): weights sum to 1, no shorts, RF clip [−80%, +120%], min trade 2%. Reconcile the cost (0.1% vs 12 bps) explicitly. Harness score = 0.4·completeness + 0.3·confidence + 0.2·feature stability + 0.1·|prediction|, with each component defined.
28. **Portfolio evaluation.** Sharpe, Sortino (MAR), Treynor, IR (TE = σ(Rp − Rb)), M², Calmar, MWROR (IRR) vs TWROR (geometric link; Modified Dietz for sub-periods), β_p.
29. **Live rules.** Encode the 7-row security/sector/NIFTY matrix (§7.13) with profile overrides. Weekly re-rate after 50% of expected return is reached. 7-day post-news wait. Breakout ranges 1y/2y/3–4y.
30. **Reconciliation log.** For every reference number reproduced, record the dataset, N, years and denominator. This addresses the notebook's recurring "inconsistent base" and "revisit & rectify" items (§9).

### Critical Files for Implementation
- ~/Ideation-data/pastwork/pdf_text/Results.txt
- ~/Ideation-data/pastwork/pdf_text/Models_Results.txt
- ~/Ideation-data/pastwork/pdf_text/Quant_Model_Data_Matrix.txt
- ~/Ideation-data/pastwork/notebook/IMG_1272.jpg (Phase II review), IMG_1260.jpg / IMG_1262.jpg (IC chain), IMG_1282.jpg / IMG_1283.jpg (BL and Ω)
- /Users/brainx/Investment Ideation Model - Personal Use/Quant_Equity_System_Dossier_v2.md
