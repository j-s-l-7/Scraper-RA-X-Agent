# Validation and Leakage Red-Team Plan: Indian-Equity Ideation System

Author: validation/leakage planner. Every number below comes from read-only probes on `equity_lake` run 2026-11-05. Nothing was written to the lake or to disk. All SQL is a sketch; implementers should port it into the `ideation` package.

---

## 0. Probe results (the empirical basis for the decisions)

### 0.1 PIT universe and fundamentals coverage

Method: rank EQ/BE symbols by trailing-90-day average turnover, keeping only symbols that traded on the anchor session. Map each symbol to `company_id` by trying, in order: ISIN (`listing_history.isin = company.isin`), then the current `nse_symbol`, then the `nse_symbol_change` chain. A symbol counts as "has fundamentals" if it has an annual Sales/Revenue/Net Profit fact with `period_end <= anchor-60d` and `> anchor-610d`.

| Anchor (session) | EQ/BE traded | Top-500 mapped/with fundamentals | Top-1000 with fundamentals | All traded with fundamentals | Mapped only via rename | ADV at rank 500 / 1000 (₹ lakh) |
|---|---|---|---|---|---|---|
| 2016-03-31 | 1,526 | 426 / **418 (83.6%)** | **804 (80.4%)** | 1,122 (73.5%) | 26 | 179 / 25 |
| 2019-03-29 | 1,635 | 449 / **449 (89.8%)** | **886 (88.6%)** | 1,338 (81.8%) | 15 | 248 / 29 |
| 2022-03-31 | 1,879 | 474 / **474 (94.8%)** | **933 (93.3%)** | 1,647 (87.7%) | 13 | 1,200 / 255 |
| 2025-03-28 | 2,306 | 486 / **486 (97.2%)** | **949 (94.9%)** | 2,033 (88.2%) | 5 | 2,993 / 545 |

Turnover units are consistent across the two bhav eras: RELIANCE `turnover_lacs` equals close×qty/1e5 within 0.3% in 2016, 2019, 2022 and 2025.

**Survivorship gap, provisional.** Top-1000, raw closes, no split adjustment. "Covered" means the name has a lake `company_id`.

| Window | Uncovered: n / median 1Y / clipped mean | Covered: n / median 1Y / clipped mean | Stopped trading in window (uncov/cov) |
|---|---|---|---|
| 2016-03→2017-03 | 200 / +17.0% / +28.5% | 800 / +24.9% / +36.9% | 8 / 12 |
| 2019-03→2020-03 | 120 / −56.1% / −49.7% | 880 / −50.8% / −43.6% | 10 / 8 |
| 2022-03→2023-03 | 75 / −2.0% / −9.4% | 925 / −11.5% / −5.6% | 13 / 11 |

Covered names beat uncovered ones by about 5–8 pp in the first two windows; the third is mixed. The 12 covered names that "stopped" in 2016 are almost certainly renames, which is a ready-made fixture for the stitching test (T-15).

**The scored set contains only survivors, by construction.** All 2,618 rows in `equity.company` are `is_active` today. No name that has fundamentals can be a delisting in the backtest. Delisting rules therefore affect only universe-level benchmarks, coverage reporting and bias measurement. They never touch a scored label. The bias is measured and disclosed, not removed.

### 0.2 Delistings and suspensions, 2015–2025

Unit: symbols in EQ/BE/BZ with `last_seen` in 2015–2025 that are not listed now.

- **Stopped trading:** 1,053 symbols. By year: 2015:102, 16:66, 17:65, 18:66, 19:78, 20:71, 21:106, 22:95, 23:150, 24:120, 25:134.
- **Renamed** (listed in `nse_symbol_change`): 355.
- **ISIN continues under another symbol:** 384, overlapping heavily with the renames.
- **"True exits"** (no rename, no ISIN continuation, no move to another series): **664**.
- **Last series before exit:** BZ 205 (non-compliant or suspended), BE 308, the rest EQ.
- **Last traded price present** in `nse_bhav_full`: 1,050 of 1,053 (99.7%).
- **Median price ratio** for true exits, last close vs about 1 year earlier: 0.83.
- **No merger or delisting event types exist.** `corporate_action` contains only dividend, split, rights and bonus, and only for current-universe companies.

### 0.3 Prices

- **`historical_prices.close` is not a raw close.** RELIANCE 2016-03-31 is 238.9 there vs 1,045.2 in bhav; 2017-09-06 is 376.1 vs raw 1,645.4. It is yfinance-adjusted with an odd extra factor (~0.914). It must not be used for PIT valuation, and it covers only the 2,617 current symbols.
- **NSE `prev_close` is not corporate-action adjusted** in either era. RELIANCE bonus ex-date 2024-10-28 shows prev_close 2,655.7 → close 1,334.35; MINDACORP split 2015-01-06 shows 95.7 → 100.4 although the 1:5 split was already applied. It cannot be used as an adjuster.
- **`adj_factor` coverage:** 1,965 symbols, every one a current `company.nse_symbol`.
  - Kinds: split 1,493, bonus 18, dividend 24,699. yfinance encodes bonuses as splits.
  - No rights factor exists, although `corporate_action` has 239 rights rows.
  - Factors are keyed on the current symbol, so pre-rename history under the old symbol gets no factor.
  - `v_prices_adjusted` is EQ-only and runs a per-row lateral subquery, which is slow.
- **Own adjustment vs yfinance, 1Y total return 2019-06-28→2020-06-30:** 1,068 covered names. 10 differ by more than 5 pp and 5 by more than 20 pp; the median absolute difference is ~0. The own factors are reliable for covered names.
- **Calendar gaps** in `nse_bhav_full` against `^NSEI` sessions: 2019-09-20 to 2019-09-30 (7 sessions, at the seam between the two bhav formats), 2021-11-04 (Muhurat) and 2025-02-01 (budget Saturday). Sessions per year are 237–251.
- **Delivery %** exists only from 2019-10-01.

### 0.4 Shares outstanding and PIT market cap

**yfinance `shares_outstanding`** has 1.95M rows over 2,556 companies. Earliest `as_of` per company is mostly 2017 (937 companies) or later. Companies with a value within 400 days before the anchor:

| Anchor | Companies |
|---|---|
| 2016-03-31 | **45** |
| 2019-03-31 | 1,170 |
| 2022-03-31 | 1,564 |

Values are raw and as-of: RELIANCE shows 3.24bn in 2015 and 5.75bn after the 2017 bonus.

**`nse_issue_size`** (face value and issue size) starts 2024-02-01, so it is unusable before then.

**Screener EPS is restated to today's share basis.** RELIANCE standalone NP/EPS gives ~1,353 crore shares in every year from FY2015 to FY2026, while the true count doubled twice. Consequences:
- Never compute P/E as raw price / screener EPS. RELIANCE FY16 would give 52.8× instead of the true ~12.4×.
- `Equity Capital` is as reported: 3,240 → 6,335 → 13,532.

**PIT market cap at 2019-03-29**, compared against method A (raw bhav close × yfinance shares, n = 1,151):

| Method | Median abs error vs A | Share within 10% of A |
|---|---|---|
| **B1:** current-basis (split-adjusted) price × standalone NP/EPS shares from the last available FY | 0.8% | 88% |
| **B2:** raw close × Equity Capital ×1e7 / face value now (no split/bonus since FY end) | 0.9% | 93% |

At 2021-03-31, Equity Capital/FV agreed with yfinance within 2% for 76% of names and within 10% for 97% (n = 984). The ₹500 cr floor passes 722 names under A and 646 under B1 at the 2019 anchor.

### 0.5 Fundamentals depth (annual Sales/Revenue, companies per `period_end` year)

- **Annual:** 2013:127, 2014:174, **2015:1,985**, 2016:2,053, 2017:2,107, 2018:2,197, 2019:2,281, 2020:2,417, 2021:2,512, 2022:2,549, 2023:2,582, 2024:2,591, 2025:2,596. FY-end month: March for 2,595 companies, December for 40, June for 21.
- **Quarterly:** at most 97 companies per year before 2023, then 2,366 or more from 2023. **There is no usable quarterly or TTM history before 2023.** TTM rows exist only with 2026 period ends (a current snapshot).
- **`shareholding`:** frequency Y from 2017 (1,754 companies); frequency Q only from 2023.
- **`earnings_event`** (yfinance) covers 500–750 companies per year from 2013. It has 189 rows dated 2027 (future scheduled events). Days from quarter end to event: p5 18, p25 27, p50 35, p75 43, p95 59, p99 78. 16% are over 45 days and 2.9% over 60 days; these include annual results (60-day lag) and the COVID-era extensions.

**The "first year" problem is real in both directions.**
- 887 companies whose first annual period is FY2014/15 were listed before 2012. Their first year is a screener 12-year-window truncation, not their true first year.
- 1,379 of the 1,425 companies listed after mid-2016 have pre-listing annuals (median 5.5 years from DRHP-era data). Their IPO year is not their first data year.

**Restatement history** (`financial_fact_history`) has 3,537 rows across 84 companies, all superseded between 2026-11-01 and 2026-11-05. Tracking started on 2026-11-01, so there is no usable history and restatement bias cannot be measured from the lake.

**`valuation_history`** (screener ratio charts, 2000–2026) is built on today's restated EPS and adjusted prices. Banned from backtests. `price_history` exists only from 2025-11. `dq_company_score` has a single as_of of 2026-11-05.

### 0.6 Macro

| Data | Coverage / status |
|---|---|
| Daily yfinance indices | Nifty 50 (`^NSEI`) from 2007-09; Nifty 500 (`^CRSLDX`) from 2005-09, **price index only, no TRI** |
| Sector indices | Most from 2011-01 or 2011-07; Fin Services from 2011-09; 3 series start only 2026-10 (unusable) |
| India VIX | From 2008-03 |
| MoSPI CPI (base 2012) | 2013-01..2025-12; the series breaks to the base-2024 series |
| MoSPI IIP (base 2011-12) | 2012-04..2026-03; series break |
| MoSPI WPI | To 2026-04 |
| All macro values | As revised today; there is no release-date column |
| `fpi_sector_fortnightly` | Starts 2020-05-31 (129 fortnights) |
| `fpi_flow_daily` | 25 rows only; live-only |

---

## 1. PIT data contract

### 1.1 Core abstraction

Every row in every local snapshot carries `available_from: date`. This is the first calendar date on which a decision taken **after the close** of that date may use the row. Anchors are always NSE sessions, and decisions are taken at the anchor's close.

```python
# ideation/pit/contract.py
@dataclass(frozen=True)
class Anchor:
    date: date             # NSE session; decision taken after its close
    horizon: str           # '1Y' | '3M' | '1M'

class PitStore:
    """Holds full local snapshots (parquet) with available_from on every row. Only entry point for features."""
    def __init__(self, snapshot_dir: Path, mode: Literal['backtest', 'live']): ...
    def as_of(self, anchor: date) -> "PitView": ...

class PitView:
    """Immutable, pre-filtered views. Every accessor returns frames with max(available_from) <= anchor."""
    anchor: date
    def fundamentals(self, period_type: Literal['A']) -> pd.DataFrame   # 'Q' allowed only if anchor >= 2023-06-30
    def prices_raw(self, lookback_sessions: int) -> pd.DataFrame        # nse_bhav_full, date <= anchor
    def prices_adj(self, lookback_sessions: int) -> pd.DataFrame        # own adjustment, factors with ex_date <= anchor ONLY
    def shares(self) -> pd.DataFrame                                    # PIT shares (sec. 1.3)
    def shareholding(self) -> pd.DataFrame
    def macro(self, codes: list[str]) -> pd.DataFrame
    def universe(self) -> pd.DataFrame                                  # sec. 2
    def _guard(self, df):  # called by every accessor
        assert df['available_from'].max() <= self.anchor, LeakageError
```

**Adjusted prices inside a view must use only factors with `ex_date <= anchor`.** Back-adjusting a 2016 price by a 2024 bonus changes the level but not returns within the lookback, so it is harmless for momentum. It is fatal for anything priced in levels (52-week-high distance, valuation), and it breaks the future-poison test. Inside `PitView`, compute `prices_adj` by back-adjusting only from the anchor.

**Structural enforcement**
1. Feature builders have the signature `def build(view: PitView) -> pd.DataFrame` and receive no DB connection. An `import-linter` contract forbids `ideation.features`, `ideation.models` and `ideation.universe` from importing `ideation.labels`, `ideation.lake` or `psycopg2`.
2. Only `ideation/lake/adapter.py` talks to Postgres. In `mode='backtest'` it refuses any table on the ban list (sec. 7, T-17) and raises `BannedSourceError`.
3. Every `PitView` frame is copied read-only (`df.flags.writeable = False` for numpy-backed columns) and is hash-logged per anchor into `ideation.run_manifest`.
4. The output frame of every feature builder carries `feature_available_from` = max over the inputs used. The pipeline asserts it is ≤ anchor.

### 1.2 Availability rules, per dataset (decided conservatively)

`deadline(period_end, lag)` = period_end + lag days. `available_from = deadline + 1 day`, because filings land late on the deadline day and the anchor decision is taken at that day's close. Overrides only ever move the date **later**.

| Dataset | Rule for `available_from` | Notes |
|---|---|---|
| Annual facts (`period_type='A'`), screener `profit-loss`, `balance-sheet`, `cash-flow`, `ratios` | `max(period_end + 60d + 1, event_date + 1)`, where the event is the first `earnings_event.event_date` in `(period_end, period_end + 120d]` with `eps_reported` not null and `event_date <= today`. Apply the COVID overrides below. | **Never** use the earlier of the two. Use the `fy_month` of each company (Dec/Jun FY-enders exist). |
| Quarterly facts (`'Q'`) | `max(period_end + 45d + 1, event_date + 1)`. For the quarter that ends the FY: +60d. | Quarterly history usable only for anchors ≥ 2023-06-30. Before that, the API raises. |
| TTM facts (`'TTM'`) | **Banned in backtests.** TTM = sum of the last 4 quarters, all available. Live mode only. | The screener TTM column is a current snapshot. |
| yfinance sections (`yf-*`) | Same lags as screener. | About 5 years deep; secondary source only. |
| `shareholding` (Q) | `period_end + 21d + 1` | Y frequency: treat the period as Mar-31, +21d. Usable from 2017. |
| Shares outstanding | yfinance: `as_of + 1`. Implied-from-financials: same as the annual fact it comes from. | See 1.3. |
| `nse_bhav_full` close/turnover at session d | `d` (EOD) | Can be used at the anchor's close. Execution happens at the **next** session. |
| `adj_factor` | `ex_date` | Back-adjust only with factors whose `ex_date <= anchor`. |
| `corporate_action` | `ex_date - 2 sessions` (record-date notice); simpler and conservative: `ex_date` | Not needed for features. |
| macro daily (yfinance) | `date` | |
| CPI (MoSPI) | `month_end(period) + 15d` | Released about the 12th of the next month. |
| WPI | `month_end + 16d` | Released about the 14th. |
| IIP | `month_end + 45d` | Released about the 12th of the second month until 2024, now about the 28th. 45 days covers both. |
| `fpi_sector_fortnightly` | `report_date + 7d` | Only from 2020-05. |
| World Bank annual | `Dec-31(year) + 270d` | |
| `analyst_estimate`, `earnings_event.eps_estimate`/`surprise_pct`, X-Agent ideas, `news_article`, `market_snapshot`, `yf_snapshot`, `fpi_flow_daily`, `dq_company_score`, `completeness` | **live-only** (`available_from = +inf` in backtest mode) | Overlays and confidence only, never features. |

**COVID statutory extensions** (`ideation/pit/overrides.py`, a data table). The implementer must verify each entry against the SEBI circulars before P1 sign-off. Until verified, apply the conservative blanket rule: **+30 days on every `period_end` in [2019-12-31, 2021-06-30]**.

| period_end | deadline used |
|---|---|
| 2019-12-31 (Q3 FY20) | normal +45 |
| 2020-03-31 (Q4/FY20) | 2020-07-31 |
| 2020-06-30 (Q1 FY21) | 2020-09-15 |
| 2021-03-31 (Q4/FY21) | 2021-06-30 |
| 2021-06-30 (Q1 FY22) | verify; default +30d blanket |

Lag helpers come from `screener_scraper/completeness.py`: `RESULT_LAG=45`, `YEAR_RESULT_LAG=60`, `SHP_LAG=21`, and `result_lag(qend, fy_month)`. Port them, don't import them, because the repo must stand alone.

```python
# ideation/pit/lags.py
def fact_available_from(period_end: date, period_type: str, fy_month: int,
                        event_date: date | None) -> date
def shp_available_from(period_end: date) -> date
def macro_available_from(code: str, period: date) -> date
```

### 1.3 PIT shares and market cap (hierarchy)

`mcap_pit(sec, anchor)` in ₹ crore. Take the first method that applies.

1. **A:** `raw_close(anchor) × yf_shares(as_of ≤ anchor, within 400d)`. If a split or bonus ex-date falls in `(as_of, anchor]`, divide the shares by the cumulative factor (factor < 1 ⇒ more shares).
2. **B1:** `adj_close_split_only(anchor) × (NP/EPS)` using **standalone** NP and EPS from the latest FY available at the anchor. Both are on today's share basis, so the pair is consistent. Requires |NP| > ₹1 cr.
3. **B2:** `raw_close × EquityCapital×1e7 / FV_at(anchor)`. FV is from `nse_issue_size` when the anchor is ≥ 2024-02, else today's FV, and only if no split or bonus ex-date falls in `(FY end, today]`.

Persist `mcap_src ∈ {A, B1, B2}`. When two methods disagree by more than 25%, take their median and set `mcap_flag`. Without fundamentals there is no market cap: uncovered and delisted names get mcap = NULL and are reported as unscorable (sec. 2).

**Valuation features always come from `mcap_pit / fundamentals`:** P/E = mcap/NP, P/S = mcap/Sales, EV = mcap + Borrowings − cash proxy. Never from price/EPS.

### 1.4 Data-completeness confidence c_i (BL Ω) must be PIT

`dq_company_score` is today-only and banned in backtests. Compute `c_i(anchor)` = share of the core features that are non-missing at the anchor × min(1, years of available annual history / 3). Store it with the features.

---

## 2. PIT universe

Build it in the `ideation` schema. `equity.universe_pit` is read-only for this system, and the scraper's `build_pit_universe` has two flaws: `avg(turnover)` averages only over days traded, and it has no min-days filter.

### 2.1 Definition (per anchor session A)

```sql
-- ideation.universe_member(anchor, security_id, symbol, series, isin, company_id, adv63_lakh, n_days63,
--                          rank_adv, mcap_pit_cr, mcap_src, listed_sessions, scorable, exclusion_reason)
with cal as (select session from ideation.calendar where session <= :A order by session desc limit 63),
liq as (
  select b.symbol,
         sum(coalesce(b.turnover_lacs,0)) / 63.0                 as adv63,     -- zero-filled over sessions
         count(*)                                                as n_days63,
         max(b.date)                                             as last_trade
  from equity.nse_bhav_full b
  where b.series in ('EQ','BE') and b.date in (select session from cal)
  group by b.symbol)
select * from liq
where n_days63 >= 50                         -- traded on >= ~80% of sessions
  and last_trade >= (select min(session) from (select session from cal limit 5) s)   -- traded in the last 5 sessions
```

Then, in Python:
- `listed_sessions` = sessions since the security's first appearance (stitched across renames and ISINs). Require at least 126 for the 1Y model (blocks IPO pops; 6M momentum defined) and at least 252 for tactical (12-1 momentum).
- `rank_adv` by adv63. Universe = top **1000** by ADV (top 500 is a reporting slice).
- Mcap floor ₹500 cr nominal, applied only where mcap exists. Names with no mcap stay in the universe as unscorable.
- `scorable` = mapped company_id AND at least one annual FY available at A AND mcap_pit ≥ 500 AND series = 'EQ' on A. BE (trade-for-trade) names are kept in the universe count but not scored; reported as `exclusion_reason='BE_series'`.
- `exclusion_reason` ∈ {`no_company`, `no_fundamentals_at_anchor`, `mcap_below_floor`, `BE_series`, `too_new`}. Every reason is counted and reported.

### 2.2 Security stitching (symbol → security_id → company_id)

`ideation.security_map(security_id, symbol, isin, valid_from, valid_to, company_id, link_method)`:
1. Seed one security per current `company` (by current ISIN).
2. Link an earlier (symbol, isin) row from `listing_history` if any of the following holds:
   - (a) same non-empty ISIN;
   - (b) an `nse_symbol_change` row old→new with `effective_date` within ±10 sessions of the old symbol's `last_seen`;
   - (c) same symbol, with the old ISIN's `last_seen` contiguous (gap ≤ 10 sessions) to the new ISIN's `first_seen`. This catches face-value splits, which change the ISIN. 557 EQ/BE symbols have more than one ISIN.
   - Reject link (c) if the gap exceeds 10 sessions; that is symbol reuse.
3. ISINs only appear in the old bhav from 2011-06-22, so pre-2011-06 rows (isin `''`) link by symbol plus contiguity.
4. All remaining symbols become their own security_id with company_id NULL.

### 2.3 Calendar

`ideation.calendar` = distinct `nse_bhav_full.date` ∪ `^NSEI` dates. Sessions missing in bhav (2019-09-20..30, 2021-11-04, 2025-02-01) are flagged `bhav_missing=true`:
- An anchor never falls on a flagged session; snap back to the previous good session.
- Labels whose entry or exit lands on one roll forward to the next good session.

### 2.4 What gets reported per anchor (`ideation.universe_coverage`)

n_traded, n_universe, n_scorable, the count for each exclusion reason, scorable share of top 500 and top 1000, and the ADV and mcap of marginal names. The P1 acceptance check reproduces the sec. 0.1 table within ±2 pp.

---

## 3. Labels

### 3.1 Timing

- Anchor A = session; features use data with `available_from <= A`.
- **Entry** = close of `s1(A)`, the first session after A. Variant: open of s1(A). The primary choice is the next-day close, because NSE opens are noisy and the closing auction is cleaner.
- **Exit** = close of `s1(A + h)` with h ∈ {1M, 3M, 12M} calendar months; `A + h` is snapped to the session on or before.
- `label_end` = exit session. `label_available_from = label_end + 1 day`. Purging uses this value.
- **Last labelable anchor:** price data ends 2026-11-03. 1Y: A ≤ 2025-10-31; for annual June anchors that means June 2025, for March anchors March 2025. 3M: A ≤ 2026-07-31. 1M: A ≤ 2026-09-30.

### 3.2 Return construction

Labels are total returns on the **stitched security**, computed in pandas from `nse_bhav_full` (EQ, BE and BZ for exits). Do not use `v_prices_adjusted`: it is EQ-only, symbol-keyed and slow.

```python
def total_return(sec_id, entry: date, exit: date) -> LabelResult:
    # daily log-returns r_t = ln(close_t / close_{t-1}) on the stitched series
    #  + ln(1/factor) on split/bonus ex-dates (adj_factor, mapped via security_map)  [covered names]
    #  + dividend: ln(1/factor_div) on ex-dates (adj_factor kind='dividend')        [covered names]
    # Uncovered names (no factors): jump detector -- overnight ratio close_t/close_{t-1} within 2% of
    #   1/k for k in {2,3,4,5,10} or (k:1 bonus) {1/2,2/3,1/3,...} AND volume_t > 2x median(20) => treat as split, flag.
    # Rights: no factor available => price-only; flag rights ex-dates for covered names (239 rows) as label_flag.
```

`LabelResult` fields: `ret_total`, `ret_price`, `exit_type ∈ {normal, stale, corp_exit, distress}`, `stale_days`, `flags`.

Cross-check: for covered names, compare against yfinance `adj_close` returns. More than 2% of names differing by over 5 pp at any anchor is a P1 acceptance failure. The probe found 0.9%.

### 3.3 Delisting, suspension and exit rules (decided)

The security has no trade in the final 20 sessions of the window and does not resume before `label_end`. Stitched renames are not exits.

| Case | Detection | Return |
|---|---|---|
| Suspension, resumes after window | gap, later trades exist | mark at last traded close (`exit_type='stale'`), record `stale_days` |
| **Distress exit** | last series BZ, OR last 60 sessions in BE with a >50% fall from entry, OR last trade more than 20 sessions before the final listing_history `last_seen` (suspension then delisting) | **−100%** (dossier sec. 2.4). Sensitivity run: last traded close. |
| **Corporate exit** (merger, buy-back delisting) | last series EQ, normal liquidity (adv63 at last trade ≥ 25% of its 1Y median), no price collapse | last traded close as the cash-out proxy; cash (0%) for the rest of the window |

Probe counts to reproduce in a unit report: 1,053 stopped, about 664 true exits, 205 last-in-BZ. In practice this affects the universe benchmark and the coverage-bias metric. The scored labels are survivors (sec. 0.1).

### 3.4 Label variants (all stored; one primary)

`ideation.label(anchor, security_id, horizon, ret_total, ret_price, excess_univ_med, excess_univ_cw, excess_n500_pr, xs_rank, exit_type, label_end, label_available_from, flags)`

- **Primary target for the 1Y RF:** `xs_rank` = cross-sectional percentile rank (0..1) of `ret_total` among the **scorable** names at the anchor. The rank is computed per anchor, which removes market level (dossier sec. 4.3 breadth).
- `excess_univ_med` = ret − median ret of the **full universe**, including uncovered and delisted names.
- `excess_univ_cw` = ret − own cap-weighted TR of the scorable universe. Weights come from mcap_pit at A.
- `excess_n500_pr` = ret − `^CRSLDX` return. This is a price index and is biased in the strategy's favour by roughly the dividend yield (≈1–1.5%/yr). It is reported, never used as a gate.
- Clipping for training targets is not needed for ranks. For bucket-return reporting, winsorize per anchor at P1/P99 and report both raw and winsorized.

---

## 4. Cross-validation

### 4.1 Feature-availability calendar (annual-only fundamentals)

Two anchor families are pre-registered. The anchor month counts as one configuration for the DSR.
- **June (primary):** last session of June. The latest FY is FY(Y), ended March of year Y, with 90 days of buffer (covers more than 99% of filers by the earnings_event lag distribution).
- **March (dossier-comparable):** 31-Mar session. The latest FY is FY(Y−1), roughly 12 months stale.

| Feature group | Needs | First June anchor | First March anchor |
|---|---|---|---|
| Levels (ROE, margins, P/S, P/E, EV/EBITDA, NetDebt/EBITDA, FCF yield) | FY t | 2015 | 2016 |
| YoY growth | FY t, t−1 (FY2014 has only 174 companies) | 2016 | 2017 |
| 3Y averages / 3Y vols | FY t−2..t | 2017 | 2018 |
| 3Y growth average | growth t−2..t | 2018 | 2019 |
| 5Y averages / 5Y growth average | FY t−4..t / growth t−4..t | 2019 / 2020 | 2020 / 2021 |
| Shareholding (Y) | 2017+ | 2017 | 2018 |
| Delivery % | 2019-10+ | 2020 | 2020 |

**Decision: drop the 5Y features from the core set.** They exist for at most 5–6 test anchors and would force a much later start. Core set = levels + YoY growth + 3Y averages and vols.

**Growth-missing rule (replaces the dossier's "first year missing"):** growth is NaN unless the prior-FY value exists, |prior| > 1e-6, and the sign is consistent for percentage growth. If the prior is ≤ 0, use the difference scaled by total assets. In addition, set growth to NaN for FYs ending within 12 months **after the listing date** (IPO-year spikes). The listing date is the stitched security's first session. Do not key the rule on the first data year: 887 companies are window-truncated and 1,379 IPOs have pre-listing data.

### 4.2 Primary walk-forward (annual anchors, 1Y label)

Purge rule: a training sample i may be used for test anchor T only if `label_available_from_i <= T`. With next-day-close exit, the label for anchor T−1 ends at s1(T) > T, **so T−1 is always purged**. Rolling train = {T−4, T−3, T−2}.

**June family (primary).** Each core-complete fold trains on 3 anchors, about 2,500–2,800 scorable rows.

| Test T | Train anchors | Purged | Notes |
|---|---|---|---|
| Jun-2019 | 2015*, 2016, 2017 | 2018 | *2015 has no growth (NaN + indicator); reported, not counted for promotion |
| Jun-2020 | 2016, 2017, 2018 | 2019 | COVID: FY2020 results available only after 2020-07-31 ⇒ most names use FY2019 at this anchor (disclosed) |
| Jun-2021 | 2017, 2018, 2019 | 2020 | FY2021 deadline 2021-06-30 ⇒ only event_date ≤ 2021-06-29 names have FY2021 |
| Jun-2022 | 2018, 2019, 2020 | 2021 | |
| Jun-2023 | 2019, 2020, 2021 | 2022 | |
| Jun-2024 | 2020, 2021, 2022 | 2023 | |
| Jun-2025 | 2021, 2022, 2023 | 2024 | label ends Jun-2026 |

That gives **6 counted test years (2020–2025) plus 1 indicative (2019)**.

**March family:** tests Mar-2021..Mar-2025, **5 counted** (train {T−4..T−2} from Mar-2017 onward), plus Mar-2020 indicative.

**Alternates (pre-registered; each is a counted configuration):**
- 2-year train {T−3, T−2}, adds one test year;
- expanding window from the first anchor.

The dossier's 3-year window is kept as primary.

### 4.3 Monthly-anchor variant for 1Y (more ICs, overlapping labels)

- Anchors: month-end sessions Jun-2016 .. Oct-2025.
- Folds: test block = the 12 monthly anchors of a calendar year, refit once per year.
- Train = the 36 monthly anchors with `label_available_from <= first test anchor`, which purges the 12 months before it.
- Embargo: none needed in forward-only WFO.
- OOS months run from about Jul-2019 to Oct-2025, roughly 76 ICs. Because labels overlap, the effective independent count is about 6. Statistics use Newey-West with lag 11 (sec. 5).

### 4.4 Tactical (1M / 3M), monthly anchors

- Features: price, volume, RRG, breadth and flows. Fundamentals from 2016 at most; delivery % from 2019-10; FPI sector from 2020-06.
- Anchors: month-end from Jan-2011 (12 months of bhav history from Jan-2010) to Sep-2026 (1M) or Jul-2026 (3M).
- Rolling 36-month train. Test = next 12 anchors. Purge h (1 or 3 months). Embargo 1 month.
- That is about 12 folds (test years 2014–2026) and about 150 OOS ICs for 1M and about 148 for 3M (3M labels overlap with lag 2).
- Feature groups whose data starts later enter as NaN with indicators. Run a sub-period report from 2020-06, when all groups exist.

### 4.5 CPCV (López de Prado) for robustness distributions

```python
def cpcv_splits(anchors: list[date], label_end: dict[date, date], n_groups: int, k_test: int,
                embargo: pd.DateOffset) -> Iterator[tuple[list[int], list[int]]]:
    # groups = contiguous anchor blocks; for each combination of k_test groups:
    # train = all other samples minus (a) purge: label interval [entry_i, label_end_i] intersects any test interval
    #                               minus (b) embargo: anchor_i in (test_block_end_label, +embargo]
```

| Horizon | Anchors | N groups | k | Splits / paths | Embargo |
|---|---|---|---|---|---|
| 1Y annual (June) | 2016–2025 (10) | 10 | 2 | 45 / 9 | 1 anchor (12M) after each test group: features of T+1 (momentum) contain the test label |
| 1Y monthly | 2016-06..2025-10 (113) | 10 | 2 | 45 / 9 | 12 months |
| 3M monthly | 2011-01..2026-07 (~187) | 12 | 2 | 66 / 11 | 3 months |
| 1M monthly | 2011-01..2026-09 (~189) | 12 | 2 | 66 / 11 | 1 month |

CPCV produces the **distribution** of OOS IC and spread Sharpe across paths, which feeds the PBO and the DSR. Promotion uses forward WFO (4.2/4.4) as the headline and CPCV as a robustness gate (sec. 5.5).

### 4.6 Preprocessing discipline

Per cross-section, fitted on that anchor's features only and legal at test time:
- within-industry z-scores with sign priors;
- the P5/P95 → median replacement;
- P1/P99 winsorization;
- median fill plus missing indicators;
- rank transforms.

Anything global is fit inside the training fold only and serialized with the fold: feature selection, RF hyperparameters, the 90/10 stability score, the isolation forest, the conformal calibration set and any scaler. The pipeline is an `sklearn.Pipeline`-like object with `fit(train_views, train_labels)` and `transform(test_view)`; it has no access to test labels.

Industry labels are today's (residual bias). Groups with fewer than 8 scorable names at an anchor fall back to sector, then to the whole cross-section.

---

## 5. Evaluation and statistics

### 5.1 Per-anchor metrics (`ideation/eval/metrics.py`)

```python
def rank_ic(score: pd.Series, label: pd.Series) -> float                 # Spearman, scorable names, NaN-safe
def bucket_returns(score, ret, q=5|10) -> pd.DataFrame                   # mean, median, n per bucket
def spread(score, ret, q=5) -> float                                     # top - bottom (mean and median)
def hit_rate(score, ret, benchmark_ret) -> float                         # share of top-bucket names beating universe median
def top_vs_bench(score, ret, bench: Literal['univ_med','univ_cw','n500_pr']) -> float
def turnover(prev_top: set, cur_top: set) -> float                       # one-way name turnover of top bucket
def cost_adjusted_spread(spread, turnover, bps=12) -> float              # dossier 12 bps per side
```

Also report IC stratified by size tercile, by industry (≥ 15 names) and by exit_type inclusion.

### 5.2 Time-series inference on the IC series

```python
def ic_summary(ic: pd.Series, horizon_months: int, anchor_spacing_months: int,
               n_boot: int = 10_000, seed: int = 0) -> dict:
    # mean, median, std, IR = mean/std, pct_positive
    # NW t-stat: HAC lag L = max(0, ceil(h/spacing) - 1)  -> annual 1Y: 0; monthly 1Y: 11; monthly 3M: 2; monthly 1M: 0
    # stationary block bootstrap (Politis-Romano) CI on mean IC, mean block length = max(L+1, round(n**(1/3)))
    # with n < 10 (annual): also exact sign test and t with n-1 df; CI from bootstrap over anchors (iid)
```

Bootstrap results are reported as 90% and 95% CIs.

**Per-anchor permutation null:** permute labels within anchor and industry, 1,000 times. This gives a per-anchor null IC distribution and a p-value, and the same machinery runs the shuffled-label test.

### 5.3 Multiple testing: trials registry and deflated Sharpe

`ideation.experiment_trial(trial_id, family, config_hash, config_json, horizon, anchor_family, n_oos, ic_series jsonb, spread_series jsonb, created_at)`. **Every** evaluated configuration is logged automatically, including failed ones. The trial count is never reset within a model family.

```python
def deflated_sharpe(sr: float, n_obs: int, skew: float, kurt: float, sr_trials: np.ndarray) -> float:
    # SR0 = sqrt(var(sr_trials)) * ((1-g)*Phi^-1(1-1/N) + g*Phi^-1(1-1/(N*e))), g=0.5772
    # DSR = Phi( (sr - SR0) * sqrt(n_obs-1) / sqrt(1 - skew*sr + (kurt-1)/4*sr^2) )
def pbo(cpcv_is_perf: np.ndarray, cpcv_oos_perf: np.ndarray) -> float   # probability of backtest overfitting (CSCV)
```

The DSR is applied to (a) the top-minus-bottom quintile spread series, cost-adjusted, and (b) the IC series (IC-IR as the "Sharpe"). N = trials in the family. The bake-off (RF, Ridge, polynomial Ridge, LightGBM, XGBoost, MLP, SVR, Phase-I linear composite), × anchor family, × train window, × feature set, each adds to N.

### 5.4 Monte Carlo and bootstrap (dossier sec. 7)

- Block-bootstrap the stitched OOS portfolio return series (block = 3 months for monthly, anchors for annual): Sharpe distribution, max-drawdown distribution, P(DD > 20%).
- Perturb window boundaries ±1 anchor, the train length {2, 3, 4}, and RF seeds (20); report the IC distribution.
- Clipping [−80%, +120%] is only for the synthetic-portfolio harness, never for evaluation.

### 5.5 Promotion gates (realistic for 6 annual test years)

The dossier's thresholds (IC > 0.05, Sharpe > 2, maxDD < 15%) are **aspirations, not gates**. The dossier's IC figures are not facts about this data. Mark a gate **B** if it is blocking and **I** if it is informational.

**1Y model, champion promotion (June family, 6 counted tests):**
1. **B:** mean OOS rank IC ≥ 0.03, the 90% bootstrap CI lower bound > 0, and IC > 0 in at least 4 of 6 tests.
2. **B:** cost-adjusted top-quintile minus `excess_univ_med` > 0 in at least 4 of 6 tests, and the mean is > 0.
3. **B:** non-inferiority against the Phase-I baseline (equal-weight sign-prior composite of the same features): mean ΔIC ≥ −0.01. A paired bootstrap of ΔIC must give P(ΔIC > 0) ≥ 0.6 for the more complex model to win; otherwise ship the baseline.
4. **B:** DSR ≥ 0.90 on the IC series, given N trials. PBO ≤ 0.5 from CPCV.
5. **B:** all leakage tests (sec. 7) green; adversarial-validation gate (sec. 6.1) green.
6. **I:** March family results, the CPCV path distribution, size/industry splits, and the coverage-bias metric (sec. 8).

**Tactical 1M/3M:**
- mean IC ≥ 0.02 with NW t ≥ 2.0;
- the 95% block-bootstrap CI excludes 0;
- DSR ≥ 0.95;
- cost-adjusted spread > 0 after 2 × 12 bps × the measured turnover;
- positive in at least 55% of months.

**Live champion change** (dossier sec. 8): the challenger's information ratio must exceed the champion's over at least 6 consecutive months of shadow scoring, with turnover ≤ 25%.

---

## 6. Integrity audits

### 6.1 Adversarial validation (per WFO fold) — B

```python
def adversarial_auc(X_train: pd.DataFrame, X_test: pd.DataFrame, seed=0) -> tuple[float, list[tuple[str,float]]]:
    # label train=0/test=1, 5-fold stratified CV with a small RF (200 trees, max_depth 6); return AUC and top-5 features
```

Run it twice: on the **model inputs**, which are per-cross-section transformed and so should give AUC ≈ 0.5, and on the **raw winsorized features**, which is expected to be high and is informational.

On model inputs: AUC ≤ 0.60 passes. Between 0.60 and 0.70 is a warning and is reported. Above 0.70 **blocks**: drop or re-transform the top shift feature, rerun, and log the rerun as a new trial.

### 6.2 Isolation-forest OOD guard — B for the live sizing rule, I in backtests

- Fit `IsolationForest(n_estimators=300, contamination='auto', random_state)` on the training-fold model inputs.
- Threshold τ = P99 of the training scores.
- At inference, any name with score > τ is marked `ood=True`, and its BL view gets **size 0**: excluded from Q, Ω → ∞.

Backtest report: share flagged per test anchor; IC and bucket return of flagged vs unflagged names. If more than 10% of the test cross-section is flagged, mark the fold "shifted" and show it.

### 6.3 Conformal intervals — I, with a calibration alert

Target = `xs_rank`. Hand-rolled, time-aware split conformal:
- Calibration residuals |y − ŷ| come from the **most recent purged OOS anchors inside the training fold**. Fit on {T−4, T−3}, predict T−2, collect residuals.
- Mondrian bins by size tercile.
- Interval at α = 0.2. Weighted variant: exponential decay over calibration anchors (Barber et al. non-exchangeable conformal), half-life 2 anchors.

Report empirical OOS coverage per anchor. The averaged coverage must sit in [0.72, 0.88]; outside that, alert. Interval width feeds Ω as `Ω_ii ∝ width_i² / c_i`, as an option. MAPIE is allowed only if pinned and the split is supplied manually; never its default K-fold, which leaks across time.

### 6.4 Friedman H — I

- Top 6 features by permutation importance gives 15 pairs.
- Subsample 300 test rows, 15-point quantile grids, computed from `sklearn.inspection.partial_dependence`.
- Cost is roughly 15 × 225 × 300 predictions per fold, which is fine.
- Report H per pair. Flag H > 0.3 on economically unrelated pairs.

### 6.5 Clustered permutation importance — I, feeds feature review

- Hierarchical clustering on the distance 1 − |Spearman| between training features, average linkage, cut at 0.5.
- Permute whole clusters within each **test anchor** cross-section, 20 repeats; importance = ΔIC.
- A cluster with negative mean ΔIC across folds goes on the review list. Sign-prior conflicts are also flagged.

### 6.6 Stability (the dossier's 90/10)

Stability = 1 − mean over folds of the rank distance between consecutive folds' cluster importances. Selection score = 0.9 × z(mean OOS IC) + 0.1 × z(stability), computed **only across trials in the registry**, never by peeking at test years beyond those already folded in.

---

## 7. Leakage test catalogue (pytest)

Tests live in `tests/leakage/`, and `pytest -m leakage` must be green before any P3+ promotion. Synthetic tests run in CI without the lake. Tests marked `lake` run locally against a parquet snapshot.

| ID | Test | Construction | Pass criterion |
|---|---|---|---|
| T-01 | `test_future_poison_features` (lake) | For anchors {2019-06-28, 2023-06-30}: build features from the snapshot. Copy the snapshot; for every row with `available_from > A` (facts, prices, macro, shares, shareholding) replace values with `rng.normal()*1e6` and shift `period_end` randomly. Also append fake future rows. Rebuild. | `pd.testing.assert_frame_equal(f0, f1, check_exact=True)`; universe membership identical |
| T-02 | `test_future_poison_scores` (lake) | Same, through a trained fold model: scores at the test anchor. | Scores identical bit-for-bit |
| T-03 | `test_past_poison_labels` | Poison prices with `date <= A`. | Labels identical; the entry is s1(A) |
| T-04 | `test_shuffled_labels` (synthetic + lake) | Permute labels within anchor (seeds 0..19); run full WFO. | |mean OOS IC| < 0.02, and 19 of 20 seeds have |t| < 2.5 |
| T-05 | `test_planted_signal_recovery` (synthetic) | 30 anchors × 800 names, 12 features N(0,1), one with `y = ρx + √(1−ρ²)ε`, ρ such that Spearman = 0.10; run the full WFO plus the RF. | Mean OOS IC ∈ [0.06, 0.14]; 90% CI excludes 0 |
| T-06 | `test_planted_leak_blocked` (synthetic) | Add feature `x_leak = label + noise`, `available_from = label_end`. | PitView raises `LeakageError`, or the feature is excluded; OOS IC of the model < 0.14 (i.e. not ≈ 0.9) |
| T-07 | `test_purge_annual` | Anchors June 2016..2025, test T = 2022. | Train set = {2018, 2019, 2020}; 2021 absent; any label with `label_available_from > T` absent |
| T-08 | `test_purge_monthly_overlap` | Monthly 1Y labels, test block starts 2022-01-31. | No train anchor > 2020-12-31 (12M purge + next-day exit); boundary anchor 2021-01-29 excluded |
| T-09 | `test_cpcv_embargo` | N=10, k=2 on annual anchors. | For each split, no train anchor within 1 anchor after a test group; all 45 splits; 9 paths cover every anchor exactly 9 times... (each group in test C(9,1)=9 times) |
| T-10 | `test_lag_annual_march_fy` | FY ending 2024-03-31, no event. | Not available on 2024-05-30; available 2024-05-31 |
| T-11 | `test_lag_dec_fy_leap` | fy_month = 12, FY ending 2023-12-31 (+60 = 2024-02-29) vs 2022-12-31 (+60 = 2023-03-01). | available_from 2024-03-01 and 2023-03-02 |
| T-12 | `test_lag_quarter_and_q4` | Q 2023-12-31 → 2024-02-15; Q 2024-03-31 for a March FY → 60-day rule → 2024-05-31. | as stated |
| T-13 | `test_lag_event_later_wins` / `test_event_earlier_ignored` | Event on day 70 → available day 71; event on day 30 → still day 61. Future-dated (2027) events ignored. | as stated |
| T-14 | `test_lag_shp_weekend`, `test_macro_lags`, `test_covid_override` | SHP 2024-03-31 + 21 = Sunday 2024-04-21 → available 2024-04-22 for anchor use (first session on or after). CPI Jan-2024 → 2024-02-15. IIP Jan-2024 → 2024-03-16 (leap) vs Jan-2023 → 2023-03-17. FY2020 annual → ≥ 2020-08-01. | as stated |
| T-15 | `test_stitching_renames` (lake) | The 12 "covered names stopped in window" at the 2016 top-1000. Each nse_symbol_change pair spanning an anchor. | Each maps to one security_id with a continuous price series; none classified as an exit |
| T-16 | `test_universe_survivorship` (lake) | Pick 3 known true exits (one BZ, one corporate exit, one distress) from the sec. 0.2 list that were in the ADV top 1000 at 2016-03-31. | Present in the universe at the anchor; counted under `no_company`; exit_type and label as in sec. 3.3 |
| T-17 | `test_static_ban` | AST scan of `ideation/{features,labels,universe,models,backtest,eval}` for string constants and SQLAlchemy table refs matching the ban list: `market_snapshot`, `yf_snapshot`, `analyst_estimate`, `valuation_history`, `dq_company_score`, `completeness`, `data_gap`, `equity.universe` (not `_pit`), `is_active`, `price_history`, `news_article`, `fpi_flow_daily`, `eps_estimate`, `surprise_pct`, `historical_prices.close` used as a valuation price, `period_type='TTM'`, `financials_asof`. Plus a runtime test: the adapter in backtest mode raises on each. | Zero hits outside the allow-listed live-overlay modules |
| T-18 | `test_no_global_fit_leak` | Monkeypatch the preprocessing `fit` to record the anchors it sees. | Every recorded anchor is in the train set of that fold, or is the test anchor itself for per-cross-section transforms; never labels from test |
| T-19 | `test_determinism` | Run fold Jun-2022 twice, plus once with `n_jobs=1` vs `-1`. | SHA-256 of the score parquet identical |
| T-20 | `test_pit_adjustment_no_future_factors` | Price level at 2016 inside the 2016 view must equal raw close × factors with ex_date ≤ 2016-03-31. | Exact |
| T-21 | `test_mcap_consistency` (lake) | At 2019-03-29: methods A vs B1. | Median abs error < 2%; ≥ 85% within 10% (probe: 0.8%, 88%) |
| T-22 | `test_label_crosscheck_yf` (lake) | Own 1Y TR vs yfinance adj_close, covered names, every June anchor. | ≤ 2% of names differ by > 5 pp |
| T-23 | `test_calendar_gaps` | An anchor requested on 2019-09-30 snaps to 2019-09-19; a label exit on 2019-09-25 rolls to 2019-10-01. | as stated |

Use `freezegun` to make sure no code path reads `date.today()` in backtest mode. A grep test bans `date.today`/`datetime.now` in `ideation/{features,labels,universe}`.

---

## 8. Residual-bias disclosure

### 8.1 Biases that cannot be removed, and how to quantify each

| # | Bias | Direction | Quantification printed |
|---|---|---|---|
| 1 | **Fundamentals exist only for today's 2,618 survivors.** Scored names never delist. | Inflates top-bucket returns and possibly IC | Scorable share of the universe per anchor (sec. 0.1: 80–95%). **Coverage-bias metric:** compute price-only signals (12-1 momentum, 6M vol, ADV) on the full PIT universe and on the scorable subset; report ΔIC and the median-return gap between covered and uncovered names per anchor (probe: +7.9 pp in 2016, +5.3 pp in 2019, −9.5 pp in 2022) |
| 2 | **Restatement:** values are as restated in Nov 2026 | Unknown, probably flattering | No data (fact history starts 2026-11-01; 3,537 changes in 4 days across 84 companies). Report "unmeasurable" and the count of tracked revisions |
| 3 | **Current sector/industry labels** | Within-industry z-scores use hindsight classification | Share of names whose industry_code is not standard. A PIT sector map is not possible; note it |
| 4 | **Screener 12-year window:** FY2015 floor; FY ≤ 2014 thin (174 cos) | Earliest anchors lack growth and averages | Feature-availability table (sec. 4.1); share of NaN per feature per anchor |
| 5 | **Pre-IPO financials** in screener (1,379 of 1,425 recent IPOs) | No look-ahead (not in the universe before listing), but DRHP numbers may be pro forma | Count of IPO-year names scored |
| 6 | **Statutory-lag proxy** instead of true filing dates (event_date for only about 25%) | Too conservative for early filers; a small leak for late filers beyond event coverage (2.9% exceed 60d) | Share of facts whose availability came from event_date vs the rule |
| 7 | **Rights issues not adjusted;** uncovered names use the split detector | Label noise | Count of rights ex-dates in windows; detector fixes per anchor |
| 8 | **Exit classification heuristic** (−100% vs last price) | Universe benchmark only | Universe median under both rules |
| 9 | **Benchmark is the Nifty 500 price index** (no TRI) | Excess vs Nifty overstated by about the dividend yield | Print both univ_cw TR and N500 PR |
| 10 | **Macro values revised today;** no release dates | Small look-ahead in revised IIP/CPI | List of macro features used; lags applied |
| 11 | **Market cap from yfinance shares / implied shares** | Floor misclassification near ₹500 cr | Disagreement share (> 25%) and mcap_src mix |
| 12 | **Tiny OOS sample** (6 annual tests) | Wide CIs | CI widths and DSR N_trials |
| 13 | **COVID filing extensions** (2020/21 anchors use older FY) | Staler features in those folds | Share of names on FY t−1 at each anchor |
| 14 | **Survivor-only `historical_prices` / `adj_factor`** | Labels for uncovered names are price-only | Share of universe labels without dividend data |

### 8.2 Standard "Data and Bias" block

Every backtest report prints this block. It is generated by `ideation/eval/bias_block.py: render_bias_block(run_id) -> str` and is mandatory: report generation fails if it is missing.

```
DATA & BIAS  (run <id>, code <git sha>, snapshot <hash>, lake as-of <ts>)
Anchors: <family> <first>..<last>; counted tests <n>; trials in family N=<N>; DSR=<x>
Universe: top-1000 ADV63, min 50/63 days, mcap>=₹500cr; scorable share by anchor: <list>
Coverage bias: covered-minus-uncovered median 1Y ret <list>; Δ IC(price-only, full vs covered) <list>
Survivorship: scored names = survivors to 2026-11 by construction (fundamentals only for today's 2,618)
Restatement: values as restated Nov-2026; unmeasurable (no history before 2026-11-01)
Sector labels: current (non-PIT)
Availability: annual +60d / quarterly +45d (+event_date if later; COVID overrides); quarterly unused before 2023
Labels: next-day close entry, total return (own factors); rights unadjusted (<n> events); exits: distress −100% (<n>), corp exit last price (<n>)
Benchmark: universe cap-weighted TR (own); Nifty 500 is price-only (div-yield gap ≈1–1.5%/yr)
Macro: revised values, fixed release lags
Live-only (not in backtest): analyst estimates, X-Agent, news, snapshots, FPI daily
```

---

## 9. Phase mapping and acceptance criteria

| Phase (architect) | Validation components | Acceptance (blocking) |
|---|---|---|
| **P0** scaffold + lake adapter + schema | `ideation/lake/adapter.py` (read-only, statement_timeout, ban list in backtest mode); snapshot-to-parquet with `available_from` columns; `ideation.calendar`; `run_manifest`, `experiment_trial` tables; `pit/lags.py` + overrides; import-linter contracts; pytest markers | T-10..T-14, T-17, T-19 (trivial pipeline) green; adapter refuses banned tables; snapshot hashes reproducible |
| **P1** PIT universe + labels + features | `security_map` stitching; `universe_member` + `universe_coverage`; PIT mcap hierarchy; label builder with exits and split detector; `PitStore/PitView` guard; growth-missing rule; c_i PIT | T-01, T-03, T-15, T-16, T-20..T-23 green; coverage table reproduces sec. 0.1 within ±2 pp; exit counts reproduce sec. 0.2 (±5%); label cross-check ≤ 2%; bias block renders with real numbers |
| **P2** regime/RRG | macro and sector-index availability lags; breadth from the full PIT universe (not scorable only) | Future-poison on regime features (T-01 extended); RRG uses only sector indices with data at the anchor (3 indices start 2026-10, excluded) |
| **P3** 1Y RF + WFO | `cv/walkforward.py`, `cv/cpcv.py`, `eval/metrics.py`, `eval/inference.py` (NW, block bootstrap, permutation null), `eval/dsr.py`, `eval/pbo.py`; Phase-I baseline; audits 6.1, 6.2, 6.4, 6.5, 6.6 | T-02, T-04..T-09, T-18 green; WFO fold table matches sec. 4.2 exactly; promotion gates in sec. 5.5 evaluated and printed (failing them is an allowed *outcome*, not a phase failure; the honest result ships) |
| **P4** tactical model | Monthly WFO and CPCV for 1M/3M; NW lags; sub-period from 2020-06 | Same leakage tests on the tactical pipeline; tactical gates printed |
| **P5** ideas engine | OOD size-0 rule; conformal intervals; live overlays (X-Agent, analyst estimates) marked `live_only` | Isolation threshold persisted per model; conformal coverage report; static ban test confirms overlays aren't imported by backtest paths |
| **P6** BL portfolio | c_i and conformal width into Ω; portfolio backtest uses universe labels incl. exits; costs of 12 bps; turnover cap | Portfolio backtest bias block; block-bootstrap Sharpe/DD distribution (dossier sec. 7); DSR on the portfolio return series with registry N |
| **P7** chat | Tools expose IC with CIs and the bias block verbatim; never a dossier IC | Golden-set eval: a question on model skill must return measured IC ± CI and cite the bias block; `untraced_numbers` empty |
| **P8** reporting | `render_bias_block`, fold tables, CPCV path plots, trials registry summary | A report is rejected without the bias block, N_trials and CIs |

### Sequencing notes and risks
- The lake is live: features must come from a **frozen parquet snapshot per run**, with the snapshot hash in the manifest. Otherwise determinism (T-19) and future-poison (T-01) are meaningless.
- `v_prices_adjusted` is slow (lateral per row). Compute cumulative factors in pandas.
- 6 annual tests is a small sample. Pre-register the configuration grid in `experiment_trial` before the first P3 run, so the DSR's N is honest.

### Critical Files for Implementation
- <source-clones>/Scraper/screener_scraper/completeness.py (statutory lags, `result_lag`, `fy_of`, `quarter_end`)
- <source-clones>/Scraper/screener_scraper/sources/nse_cm_bhav.py (`build_pit_universe`; days-traded averaging flaw)
- <source-clones>/Scraper/sql/103_listing_history.sql (listing_history / universe_pit schema)
- <source-clones>/Scraper/sql/104_adj_factor.sql (adj_factor semantics; `v_prices_adjusted` is EQ-only)
- <source-clones>/Scraper/sql/080_pit.sql (`financials_asof`: tracking began 2026-11-01, unusable for history)
- /Users/brainx/Investment Ideation Model - Personal Use/Quant_Equity_System_Dossier_v2.md (sections 2.3–2.4, 4.4, 7, 9)