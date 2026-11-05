# Scraper-RA-X-Agent — Investment Ideation System

Private, personal-use system that generates **Indian-equity investment ideas** (1-year and tactical 1–3-month horizons) and
**Black-Litterman model portfolios** (including small-capital tiers), built on:

- **Scraper** (`j-s-l-7/Scraper`) — the local equity lake (`equity`/`macro` schemas, read-only to this system)
- **Quant Equity System Dossier v2** — the methodology (RF alpha model, regime, RRG, BL, validation)
- **Rebirth Assistant** (`j-s-l-7/rebirth-assistant`) — the chat-copilot pattern (tools supply every number)
- **X-Agent** (`j-s-l-7/x-agent`) — live sentiment overlay (never trained on, never backtested)

Design: see [`docs/plan/`](docs/plan/). Read `03_RECONCILED_DECISIONS.md` first; it overrides 01/02 where they conflict.
Build status: [`docs/STATUS.md`](docs/STATUS.md).

> Personal research tool, not investment advice. Model rankings are probabilistic and backtests carry residual biases
> (restated financials, current sector labels, partial survivorship) that every report discloses.

## Setup (Phase 0)

```sh
uv sync                                   # Python 3.12, exact pins from uv.lock
git config core.hooksPath .githooks       # pre-push: secret/data scan + offline tests
uv run ideation bootstrap-role --yes      # ONE-TIME, as the lake superuser: role ideation_rw (03 §C1)
uv run ideation init-db                   # ideation schema migrations (idempotent)
uv run ideation doctor                    # lake access, read-only, RLS bypass, schema, lags, freshness
uv run pytest -m "not lake"               # offline suite;  -m lake  runs against the local lake
```

Configuration: `IDEATION_*` environment variables (see `.env.example`), with methodology parameters in `config/*.yaml`
(hashed into every run's `config_hash`). Local artifacts live in `IDEATION_DATA_DIR` (default `~/Ideation-data`,
outside the repo). An optional private env file (`IDEATION_SECRETS_FILE`, default `~/Ideation-data/.secrets.env`) is
loaded when present.
