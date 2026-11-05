# Context brief — Investment Ideation System (Scraper + Rebirth Assistant + X-Agent + Quant Dossier)

Date: 2026-11-05. Owner: personal use (single user, Indian equities investor/researcher).

## Goal
Build an intelligent system that generates **investment ideas for Indian listed equities**, considering current
market dynamics (regime, breadth, sector rotation, macro, flows, sentiment), the investment **time horizon**, and
portfolio **structure** (risk profile, diversification, constraints). It must be **robust, free of look-ahead /
leakage, and statistically sound**. Methodology blueprint = the Quant Equity System Dossier.

## User decisions (final)
- Target repo `j-s-l-7/Scraper-RA-X-Agent` (now PRIVATE). Local clone:
  `/Users/brainx/Investment Ideation Model - Personal Use/Scraper-RA-X-Agent` (empty except README).
- Outputs go to a **new `ideation` schema in the local lake Postgres** (`postgresql://localhost:5433/equity_lake`).
  `equity` and `macro` schemas are READ-ONLY to this system (a scraper fleet writes them live). Supabase is NEVER touched.
- Milestone scope: **(1) stock ideas (core)** and **(2) portfolio construction (Black-Litterman)**.
  NOT in scope now: personal-holdings review (Phase IV nudges), mutual funds.
- Horizons: **1 year** (dossier Phase II target) and **tactical 1–3 months** (momentum / RRG / breakout / flows driven).
- Chat assistance: pattern from Rebirth Assistant (RA). LLM = Anthropic Claude.
- Planning agents run on Fable; implementation agents on Opus.

## Source 1 — Quant dossier (methodology)
`/Users/brainx/Investment Ideation Model - Personal Use/Quant_Equity_System_Dossier_v2.md` (601 lines). READ IT.
Key: Spearman rank IC as skill measure; within-industry z-scores with sign priors; 11 base + 9 derived features
(ROE/ROA/ROIC/OpMargin, Rev/EPS growth, P/S, P/E, EV/EBITDA, P/FCF, NetDebt/EBITDA, 3Y/5Y averages, vol of margins/ROE/ROIC, FCF yield);
cleaning (first-year growth missing, P5/P95 → median, winsorize P1/P99); Phase I linear (descriptive only);
Phase II Random Forest selected, 3y rolling train / 1y test, 90/10 performance-vs-stability selection;
Platinum audit (Friedman H, adversarial validation, isolation-forest OOD guard → size 0);
Phase III Black-Litterman with CAPM prior (Rf≈7%, E[Rm]≈12%, beta 252d min 60 clipped [-1,3] fallback 1.0),
Ω_ii = τσ_i² scaled by data-completeness confidence c_i, τ=0.05, Ledoit-Wolf/OAS + ridge 1e-5,
constraints (per-security cap 5–25% by style, stop-loss by style, turnover cap 20–25%, 12 bps cost, ₹500 cr micro-cap floor,
risk aversion dynamic per profile), nudge mapping Δw→Load up/Add/Hold/Trim/Exit;
RRG sector rotation (RS-Ratio, RS-Momentum, 4 quadrants); §4.3 breadth divergence (median stock vs index);
§7 WFO + Monte Carlo/bootstrapping + synthetic portfolios; §8 architecture; §9 upgrades (ALE, H-stat, conformal/MAPIE,
adversarial validation, CPCV purged/embargoed, clustered feature importance, isolation score).
The dossier's IC numbers came from a DIFFERENT dataset (Bloomberg-like). Do NOT carry them over as facts; the system must
measure IC honestly on the lake.

## Source 2 — Scraper (data layer). Repo clone (read-only reference):
`<source-clones>/Scraper`
Live local working copy (has .venv with psycopg2): `~/Scraper - Screener.in` (quote the path). Do NOT modify it.
Read ARCHITECTURE.md, CLAUDE.md, sql/*.sql, screener_scraper/completeness.py (statutory lags LODR 33: 45 days quarterly,
60 days annual; LODR 31: 21 days shareholding), library/ (FTS5 search over annual reports + concalls in data/library.sqlite, 2.4 GB).

Lake = local Postgres 15 at `postgresql://localhost:5433/equity_lake` (10 GB). Query with
`~/"Scraper - Screener.in"/.venv/bin/python` + psycopg2, `set_session(readonly=True)`, `statement_timeout`.
Schemas: `equity`, `macro`, `public` (lake bookkeeping). Key tables (approx rows):
- equity.company (2,618): company_id, nse_symbol, bse_code, isin, sector, industry_group, industry, sub_industry (CURRENT labels, not PIT), is_active.
  Sectors (screener taxonomy): Consumer Discretionary 720, Industrials 512, Commodities 310, Financial Services, ...
- equity.universe: TOP1000_MCAP (2,618 rows, as_of 2026-11-01 only), TOP100_MCAP. Selected by TODAY's market cap ⇒ survivorship.
- equity.universe_pit: EXISTS BUT EMPTY (0 rows). equity.listing_history: 16,369 rows (symbol, isin, series, first_seen, last_seen, is_listed_now), 8,424 ISINs, 2010-01-04..2026-11-03 — all symbols ever seen in NSE bhavcopies.
- equity.nse_bhav_full (8.2M): symbol, series, date, prev_close, open, high, low, last, close, avg_price, ttl_trd_qnty, turnover_lacs, no_of_trades, deliv_qty, deliv_per — includes delisted names (2010+).
- equity.bse_bhav (8.2M), equity.fo_bhav (1.1M F&O), equity.bulk_block_deal (249).
- equity.historical_prices (6.9M): symbol, date, open, high, low, close, adj_close, volume, source; 1995-07-10..2026-11-05; 2,618 symbols (current universe only).
  equity.v_prices_adjusted, equity.adj_factor (split/bonus/dividend factors).
- equity.financial_fact (7.6M): company_id, basis (C/S), metric_id, period_type (A/Q/H/TTM), period_end, value, run_id, updated_at.
  Annual coverage by FY: 2008:68, 2010:80, 2012:145, 2014:201, **2015:2025**, 2016:2085, 2018:2206, 2020:2423, 2022:2566, 2024:2604, 2025:2606, 2026:2575.
  ⇒ broad cross-sections only from FY2015 (screener shows 12 FY). Quarterly: screener ~13 quarters (+ yfinance ~5).
  NO filing/announcement date on facts. updated_at = scrape time (Nov 2026), so equity.financials_asof() cannot reconstruct
  history before tracking began. Values are as restated today.
- equity.metric (468): metric_id, section, name, parent_metric_id, unit, canonical_code (UNPOPULATED), source.
  Screener top-level lines — profit-loss: Sales; Expenses; Operating Profit; OPM %; Other Income; Interest; Depreciation;
  Profit before tax; Tax %; Net Profit; EPS in Rs; Dividend Payout %; Revenue; Financing Profit; Financing Margin % (banks/NBFCs);
  balance-sheet: Equity Capital; Reserves; Borrowings; Other Liabilities; Total Liabilities; Fixed Assets; CWIP; Investments;
  Other Assets; Total Assets; Deposits; Borrowing (banks); cash-flow: Cash from Operating/Investing/Financing Activity; Net Cash Flow;
  Free Cash Flow; CFO/EBITDA; ratios: Debtor Days; Inventory Days; Days Payable; Cash Conversion Cycle; Working Capital Days; ROCE %; ROE %;
  quarters: same as P&L + Gross NPA %, Net NPA %, YoY Sales growth. Plus child schedule lines; yfinance sections yf-income/yf-balance/yf-cashflow (~5y).
  ⇒ ROIC, EV/EBITDA, P/FCF, NetDebt/EBITDA etc. must be DERIVED with an explicit mapping; banks/financials need their own mapping.
- equity.shares_outstanding (1.95M): company_id, as_of, shares, source. equity.nse_issue_size change log.
- equity.valuation_history (3.6M), equity.price_history (screener weekly/daily + DMA), equity.market_snapshot / yf_snapshot (CURRENT snapshots — never use in backtests).
- equity.shareholding (267k: promoters/fii/dii/public by quarter), shareholding_holder (1M), holder.
- equity.analyst_estimate (364k, yfinance; no history ⇒ live only), earnings_event (29k: event_date, eps_estimate, eps_reported, surprise_pct),
  insider_transaction (20k), corporate_action (26k), credit_rating (259), document (92k) / document_link (124k), news_article (empty).
- equity.completeness / data_gap / dq_company_score (2,618) — data-quality per company ⇒ use as data-completeness proxy c_i.
- macro.series (171 codes; code,name,source,unit,freq,base_year), macro.series_daily(code,date,value), macro.series_monthly(code,period,value),
  macro.series_annual. Daily (yfinance): Nifty 50/100/200/500, sector indices ^CNXAUTO ^CNXFMCG ^CNXPHARMA ^CNXMETAL ^CNXREALTY
  ^CNXPSUBANK ^CNXINFRA ^CNXPSE ^CNXMNC ^CNXCONSUM ^NSEBANK ^CNXIT NIFTY_FIN_SERVICE.NS, Next 50, Midcap 50, Sensex, India VIX, VIX,
  USD/INR, WTI, gold, US10Y. Monthly MoSPI: IIP (base 2011-12, sectoral), CPI (base 2012 and 2024 separate). Annual World Bank.
  macro.fpi_flow_daily (25 rows only), macro.fpi_sector_fortnightly (100k).
  Macro series need release lags in any backtest.

## Source 3 — Rebirth Assistant (chat pattern). Clone:
`.../scratchpad/src/rebirth-assistant` — backend/app/copilot/{orchestrator.py, tools.py, prompts.py, guardrails.py, compliance.py},
api/chat.py, FastAPI + SQLAlchemy, Next.js frontend, evals/golden_set.jsonl + run_evals.py.
Pattern to reuse: **the LLM explains; deterministic tools supply every number**; append-only transcript; compliance post-processing;
`untraced_numbers` (log any number in the answer that no tool output supports); evals golden set.
Do NOT reuse its guardrails verbatim: they route stocks/F&O to "out of scope" (opposite of this system). Its data is MF on Supabase (out of scope).
RA's copilot uses claude-fable-5-1 with tool use + streaming. ANTHROPIC_API_KEY exists in RA's local backend/.env.

## Source 4 — X-Agent (sentiment overlay). Clone: `.../scratchpad/src/x-agent` (xagent.py, ideas.py, market.py, people.json).
Local: `~/X-Agent/data/xagent.db` (SQLite) — tables: users, tweets, refs, usage, user_usage, processed,
ideas(id, tweet_id, username, posted_at, kind[idea|principle], subject, subject_type, ticker, market, direction[bullish|bearish|avoid|watch],
thesis, horizon, catalysts, risks, evidence, confidence, speculative, model, extracted_at). Weekly reports/week_ideas.csv
(vs_last_week: continues/contradicts/new/faded; consensus/contrarian) — not generated yet. Accounts: Gautam__Baid (India markets),
aravind (geopolitics, speculative). X budget ~100 posts/month ⇒ sparse, NO history ⇒ can only be a LIVE overlay (adjust Ω/confidence,
flag consensus/contrarian), never a trained feature, never in a backtest.

## Hard requirements: leakage / soundness checklist
1. Availability date = period_end + statutory lag (quarter 45d, annual 60d; reuse completeness.py logic). A 31-Mar anchor cannot use the FY ending that day.
2. Survivorship: lake companies were chosen by TODAY's mcap. Build anchor-date universes from nse_bhav_full turnover + PIT mcap
   (close × PIT shares) + listing_history; report fundamentals coverage share of the PIT universe; delisted names per dossier §2.4
   (−100% or last traded price), not dropped.
3. Restatement bias: values are today's restated numbers; cannot be removed ⇒ disclose as residual bias in every backtest report.
4. Valuation at anchor = raw close × PIT shares outstanding with only already-available fundamentals. Never market_snapshot / yf_snapshot.
5. Sector labels are current, not PIT ⇒ residual bias, disclosed.
6. Purge + embargo overlapping labels (1Y labels with monthly/quarterly anchors overlap; tactical 1–3M too). CPCV per dossier §9.
   z-scores / winsorization / median-fill fitted per cross-section or on training window only.
7. Macro: release lags.
8. X-Agent, news, analyst estimates: no history ⇒ live overlays only.
9. Tests: "future-poison" test (corrupt every value dated after anchor ⇒ scores unchanged); shuffled-label test (IC ≈ 0);
   determinism (fixed seeds).
10. Report IC honestly with confidence intervals; never hard-code dossier results.

## Engineering constraints
- Python 3.12+ (system has 3.14; Scraper .venv exists). Prefer uv or venv inside the repo.
- Repo must never contain data, secrets, .env, *.db, *.sqlite, *.parquet, *.jsonl.gz, reports. Tijori terms forbid redistribution.
- Lake is 10 GB and live: read-only connection, statement_timeout, snapshot needed features to local parquet once per run.
- Work is committed and pushed phase by phase to the private repo.
