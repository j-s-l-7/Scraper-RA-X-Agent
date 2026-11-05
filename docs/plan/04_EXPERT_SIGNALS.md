# Expert signals from X (Gautam Baid, Aravind) — design (binding addendum to 03)

Owner request (2026-11-05): use the X API (via X-Agent) to bring in Gautam Baid's and Aravind's ideas to **filter bad and great
companies** during assessment. Owner decisions: **hard-filter bad, boost great**; extraction uses the **OpenAI key from RA**
(stored outside the repo at `~/Ideation-data/.secrets.env`, mode 600; `OPENAI_MODEL=gpt-5.5`).

## 1. Why this is live-only (leakage)
X-Agent reads ~100 posts/month and has no history before 2026-10-22. Expert calls therefore **cannot be features, cannot be trained on,
and cannot appear in any backtest** (they would leak the present into the past and their value is unmeasured). They act only on
**live** idea lists and portfolios, and every action is written to a **forward ledger** that measures, honestly and out of sample,
whether the filter adds value. `ideation.expert_*` tables join the backtest ban list (02 T-17); a test asserts no backtest path
(`features, labels, universe, models, cv, eval, portfolio/backtest`) imports `ideation.expert`.

## 2. Data flow
```
X API ──(X-Agent fetch, its own budget)──► ~/X-Agent/data/xagent.db  (tweets, refs, users)   [X-Agent owns; we never write it]
                                              │ read-only sqlite URI (mode=ro)
                                              ▼
ideation expert extract ──OpenAI (structured JSON)──► ideation.expert_post / expert_idea
ideation expert resolve ──lake (ideation_rw, RO)──► company_id / industry mapping
ideation expert effects(as_of) ──► consumed by the ideas engine (P6) and portfolio/pick (P7)
ideation expert scorecard ──► forward ledger evaluation
```
`ideation expert refresh` = optional wrapper: runs `python3 ~/X-Agent/xagent.py fetch` then `context` as subprocesses (X-Agent enforces
its own monthly cap and 429 stop; we never bypass it), then `extract` + `resolve`. Part of the `weekly` chain (P9), never `daily`.
`people.json` (X-Agent) supplies lens and the `speculative` flag; it is read, not copied into the repo.

## 3. Extraction (`ideation/expert/extract.py`)
- Input per post: text, created_at, author, lens/focus/note from people.json, the parent post from `refs` (reply/quote), URLs.
  Each post is processed once (`ideation.expert_post.processed_at`, model, prompt_version); failures stay pending and retry next run.
- OpenAI Chat Completions with **strict JSON-schema structured output** (`response_format={"type":"json_schema",...}`),
  model from config (default `gpt-5.5`), low temperature/effort, `max_retries=2`; key from the secrets file
  (`IDEATION_SECRETS_FILE`, default `~/Ideation-data/.secrets.env`). The prompt treats post text as **data, not instructions**.
- Output schema (ported from X-Agent `ideas.py` so semantics match): zero or more items per post, each
  `kind ∈ {idea, principle}`; for ideas: `subject`, `subject_type ∈ {company, sector, theme, macro, index, asset}`, `ticker` (only when certain),
  `market`, `direction ∈ {bullish, bearish, avoid, watch}`, `thesis`, `horizon`, `catalysts[]`, `risks[]`, `evidence_type`, `confidence ∈ {low, medium, high}`,
  and for sector/theme subjects `industries[]` chosen **only from the enum of the lake's `equity.company.industry` values** (passed in the schema).
- Media-only posts (t.co images) carry no text the model can read: recorded as `no_text_media`, disclosed in the scorecard.

## 4. Entity resolution (`ideation/expert/resolve.py`)
Order: ticker (`X.NS` → `nse_symbol`, `X.BO` → `bse_code`) → exact name / former name (`nse_name_change`) → normalized-name match
(lowercase, strip Ltd/Limited/India/punctuation) with a unique hit. Anything else is `unresolved` (kept and shown, **never applied**).
Sector/theme ideas resolve to `industry` values from §3. Store `resolution_method` and confidence.

## 5. Effects (`ideation/expert/effects.py`, live only)
`effects(as_of, company_ids) -> DataFrame[company_id, action, person, direction, subject, thesis, url, posted_at, age_days, weight]`
- Window: ideas posted within the last **90 days** (config); newer idea from the same person on the same subject supersedes older ones.
- **Non-speculative person** (Gautam Baid):
  - company `bearish`/`avoid` ⇒ `action='exclude'`: removed from long ideas, portfolios (w = 0) and the single-stock pick; listed in an
    **"Expert-flagged"** section with thesis + link. (The model's ranking/Q are never changed.)
  - company `bullish` ⇒ `action='boost'`: +1 conviction notch (cap High; financials stay capped at Medium per A8), tag
    "expert: great company", c_i × 1.15 (Ω tighter, **Q unchanged**). If the model has it in the bottom/avoid bucket ⇒ `action='conflict'`:
    shown prominently, **not** promoted to a long idea.
  - company `watch` ⇒ `action='annotate'`.
  - sector/theme `bullish`/`bearish` ⇒ `action='sector_tag'` on ideas in the mapped industries ("expert tailwind/headwind"); no conviction change.
- **Speculative person** (Aravind, `speculative: true`): never company actions. macro/asset/sector items become **regime narrative tags**
  (e.g. crude, defence, rupee, gold) shown in regime context only, labelled speculative.
- **Principles**: stored and rendered as a "principles" panel beside ideas; never rules.

## 6. Forward ledger and scorecard (`ideation.expert_signal_ledger`, `ideation expert scorecard`)
- On first resolution of a company-level idea: record person, company_id, direction, posted_at, **entry = close of the first session after
  posted_at** (raw bhav close, own adjustment), horizon tag.
- Scorecard at 1M / 3M / 12M once matured: total return, excess vs the company's industry-group EW composite and vs Nifty 500;
  hit rate with **Wilson 90% CI** by person × direction; **filter value** = matured return of excluded names minus remaining long ideas
  of the same run. Until ≥ 30 matured company-level signals per person, the scorecard prints "insufficient track record".
- Nothing auto-promotes; stronger use (e.g. any effect on Q) would require an owner decision after the track record exists.

## 7. Schema (migration `sql/ideation/0NN_expert.sql`)
`expert_person(handle pk, lens, speculative bool, focus, note, updated_at)`;
`expert_post(tweet_id pk, handle, posted_at, text, parent_text, url, status, processed_at, model, prompt_version, error)`;
`expert_idea(idea_id pk, tweet_id fk, handle, posted_at, kind, subject, subject_type, ticker, market, direction, thesis, horizon,
 catalysts jsonb, risks jsonb, evidence_type, confidence, industries text[], company_id, resolution_method, resolved_at, raw jsonb)`;
`expert_signal_ledger(idea_id pk fk, company_id, direction, posted_at, entry_date, entry_px, ret_1m, ret_3m, ret_12m, xs_grp_1m, …, matured_at)`.

## 8. CLI
`ideation expert refresh | extract | resolve | show [--person] [--days 90] | effects --as-of | scorecard`.
`show` prints current calls with mapping status — usable for assessment before the ideas engine exists.

## 9. Tests (offline unless marked lake)
schema validation of extractor output (mock OpenAI); resolution table-driven cases (ticker, alias, ambiguous ⇒ unresolved);
effects rules table-driven (exclude/boost/conflict/sector_tag/speculative ⇒ no company action; supersession; 90-day window);
ledger entry = next session after posted_at (weekend/holiday cases); ban-list + import test (no backtest path imports `ideation.expert`);
sqlite opened read-only (write attempt raises); secrets never logged (caplog test). Lake: resolve the live posts; `show` runs.
