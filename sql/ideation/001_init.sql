-- 001: core ideation schema (01 §1.3). Idempotent: every object uses IF NOT EXISTS / OR REPLACE, and every index
-- is named, so a rerun never duplicates it. Applied by `ideation init-db` as ideation_rw, which owns the schema.
-- Changes from the 01 outline:
--   * schema_migration gains name + checksum (init-db refuses an applied file whose text changed);
--   * run.status gains 'superseded' (an ok run re-executed with --force demotes the previous ok row, which keeps
--     the unique "one ok run per run_key" index valid);
--   * the partial unique indexes are named; v_latest_ideas is concrete SQL.

create schema if not exists ideation;

create table if not exists ideation.schema_migration (
  version    int primary key,
  name       text not null,
  checksum   text not null,
  applied_at timestamptz not null default now()
);

-- one row per CLI invocation; run_key = sha256(kind, as_of, snap_id, config_hash, normalized args)
create table if not exists ideation.run (
  run_id      bigserial primary key,
  kind        text not null,      -- snapshot|features|train|backtest|predict|regime|ideas|portfolio|report|daily|weekly
  as_of       date,
  snap_id     text,
  run_key     text not null,
  status      text not null check (status in ('running', 'ok', 'failed', 'skipped', 'superseded')),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  git_sha     text,
  config_hash text,
  params      jsonb,
  stats       jsonb,
  error       text,
  host        text
);
create unique index if not exists run_key_ok_uq on ideation.run (run_key) where status = 'ok';
create index if not exists run_kind_started_idx on ideation.run (kind, started_at desc);

create table if not exists ideation.snapshot (
  snap_id          text primary key,
  run_id           bigint references ideation.run,
  lake_max_bhav    date,
  lake_max_fact_ts timestamptz,
  manifest         jsonb,
  path             text,
  created_at       timestamptz not null default now()
);

create table if not exists ideation.feature_set (
  feature_set_id serial primary key,
  name           text,
  horizon        text,
  version        int,
  columns        jsonb,
  mapping_sha    text,
  unique (name, version)
);

create table if not exists ideation.model (
  model_id       serial primary key,
  horizon        text,
  family         text,             -- rf|ridge|lgbm|composite
  feature_set_id int references ideation.feature_set,
  train_anchors  daterange,
  params         jsonb,
  artifact_path  text,
  artifact_sha   text,
  selection      jsonb,            -- performance, stability, 90/10 score
  status         text check (status in ('candidate', 'champion', 'retired')),
  run_id         bigint,
  created_at     timestamptz not null default now()
);
create unique index if not exists model_champion_uq on ideation.model (horizon) where status = 'champion';

create table if not exists ideation.wf_result (
  model_id     int references ideation.model,
  test_anchor  date,
  n            int,
  n_universe   int,
  ic           real,
  ic_lo        real,
  ic_hi        real,
  spread_tb    real,
  hit_top      real,
  top_ret      real,
  bottom_ret   real,
  bench_ret    real,
  unscored_ret real,
  coverage     jsonb,
  primary key (model_id, test_anchor)
);

create table if not exists ideation.prediction (
  run_id     bigint,
  model_id   int,
  horizon    text,
  anchor     date,
  company_id int,
  symbol     text,
  score      real,
  rank_pct   real,
  alpha      real,
  exp_ret    real,
  lo         real,
  hi         real,
  ood_score  real,
  ood        boolean,
  c_conf     real,
  contrib    jsonb,
  primary key (run_id, horizon, company_id)
);

create table if not exists ideation.regime_snapshot (
  as_of   date primary key,
  run_id  bigint,
  metrics jsonb,
  labels  jsonb
);

create table if not exists ideation.sector_rotation (
  as_of      date,
  group_type text,
  group_code text,
  rs_ratio   real,
  rs_mom     real,
  quadrant   text,
  n_members  int,
  run_id     bigint,
  primary key (as_of, group_type, group_code)
);

create table if not exists ideation.idea (
  idea_id      bigserial primary key,
  run_id       bigint,
  as_of        date,
  horizon      text,
  company_id   int,
  symbol       text,
  side         text check (side in ('long', 'avoid')),
  rank         int,
  conviction   text,
  score        real,
  exp_ret_lo   real,
  exp_ret_mid  real,
  exp_ret_hi   real,
  thesis       jsonb,
  risks        jsonb,
  invalidation jsonb,
  sector_ctx   jsonb,
  regime_ctx   jsonb,
  overlay      jsonb,
  flags        text[],
  continuity   text,
  prev_idea_id bigint,
  unique (run_id, horizon, company_id)
);

create or replace view ideation.v_latest_ideas as
select i.*
from ideation.idea i
where i.run_id = (select max(r.run_id) from ideation.run r where r.kind = 'ideas' and r.status = 'ok');

create table if not exists ideation.portfolio (
  portfolio_id      bigserial primary key,
  run_id            bigint,
  as_of             date,
  profile           text,
  horizon           text,
  capital_inr       numeric,
  params            jsonb,
  metrics           jsonb,
  prev_portfolio_id bigint
);

create table if not exists ideation.portfolio_weight (
  portfolio_id bigint references ideation.portfolio,
  company_id   int,
  symbol       text,
  w            real,
  w_prev       real,
  pi           real,
  q            real,
  omega        real,
  mu_bl        real,
  cap          real,
  action       text,
  primary key (portfolio_id, company_id)
);

create table if not exists ideation.backtest (
  backtest_id bigserial primary key,
  run_id      bigint,
  kind        text,                -- ic|portfolio|mc|pick
  horizon     text,
  profile     text,
  config      jsonb,
  period      daterange,
  summary     jsonb,
  disclosures text[],
  report_path text
);

create table if not exists ideation.backtest_period (
  backtest_id  bigint references ideation.backtest,
  period_start date,
  period_end   date,
  port_ret     real,
  bench_ret    real,
  ew_univ_ret  real,
  turnover     real,
  n_holdings   int,
  extra        jsonb,
  primary key (backtest_id, period_start)
);

create table if not exists ideation.chat_session (
  session_id     uuid primary key,
  created_at     timestamptz not null default now(),
  title          text,
  model          text,
  prompt_version text,
  system_prompt  text
);

create table if not exists ideation.chat_message (
  session_id   uuid references ideation.chat_session,
  seq          int,
  role         text,
  api_content  jsonb,
  display_text text,
  visible      boolean,
  tool_calls   jsonb,
  guardrail    jsonb,
  flags        jsonb,
  untraced     jsonb,
  disclosures  text[],
  usage        jsonb,
  created_at   timestamptz not null default now(),
  primary key (session_id, seq)
);
