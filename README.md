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

## Status

| Done (merged to `main`) | Next |
|---|---|
| P0 scaffold, lake adapter, `ideation` schema · P1 snapshot, security map, PIT universes (`U_score`, `U_score_T`), labels with sealed holdout · E1 evaluation stack, model zoo, ranking mechanism, trials registry · P2 fundamental features · PT1 earnings-call text extraction | P3 technical / regime features · PT2 text features · 1Y and tactical model trials · research report · then the ideas engine (stock recommendations), Black-Litterman portfolios, chat, API |

Stock recommendations come **after** the research report: no model has been trained yet, so the system does not output
picks today. Details and measured numbers per phase: [`docs/STATUS.md`](docs/STATUS.md).

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

## Pipeline commands (run from `main`; only `main` applies migrations)

```sh
uv run ideation snapshot --full                              # one read-only lake transaction -> parquet snapshot
uv run ideation features build --anchors june,march --force  # PIT fundamental features (P2)
uv run ideation universe build --anchors june,march,monthly --force   # U_score / U_score_T per anchor
uv run ideation labels build --horizon 1Y,3M,1M --anchors june,march,monthly --force
uv run ideation text extract --types concall_transcript,concall_ppt   # transcript / presentation signals (PT1)
uv run ideation coverage                                     # scored-N per anchor
uv run ideation trials register --family 1y                  # pre-register model configs before any fit
```

Rebuild order after a new snapshot or feature change: snapshot -> features -> universe -> labels. Labels at the
holdout anchors (1Y from 2025-03) are stored but sealed; read labels only through `ideation.labels.loader.load_labels`.

## Layout

`ideation/` — `lake/` (read-only adapter, queries, bans, snapshot) · `pit/` (availability lags, `PitStore`/`PitView`) ·
`universe/` · `labels/` · `features/` · `text/` · `cv/` · `eval/` · `models/` · `rank/` · `trials/` · `db/` (guarded
writes, migrations). `config/*.yaml` methodology and trial lists · `sql/ideation/` numbered migrations · `tests/`
(`unit`, `leakage`, `lake`) · `docs/plan/` binding design.

Phases run in git worktrees (`../wt-<phase>`); a branch tests DDL in the `ddl_sandbox` fixture and never runs `init-db`.
