-- 002: PIT / validation tables from 02 (§1.1, §2.1–2.4, §3.4, §5.3) with 03 §C3 label timing. Idempotent.

-- 02 §1.1 point 3: every PitView frame is hash-logged per anchor; snapshot files are logged with anchor NULL.
create table if not exists ideation.run_manifest (
  run_id             bigint not null references ideation.run,
  anchor             date,
  item               text not null,          -- dataset or PitView accessor, e.g. 'fundamentals', 'prices_adj'
  n_rows             bigint,
  sha256             text not null,
  max_available_from date,                   -- must be <= anchor for every PitView frame
  created_at         timestamptz not null default now(),
  constraint run_manifest_uq unique nulls not distinct (run_id, anchor, item)
);

-- 02 §5.3: every evaluated configuration, including failed ones; never reset within a family (honest DSR N).
create table if not exists ideation.experiment_trial (
  trial_id      bigserial primary key,
  family        text not null,               -- e.g. '1Y-june', 'tactical-3M'
  config_hash   text not null,
  config_json   jsonb not null,
  horizon       text,
  anchor_family text,                        -- 'june' | 'march' | 'monthly'
  status        text not null default 'registered' check (status in ('registered', 'ok', 'failed')),
  n_oos         int,
  ic_series     jsonb,
  spread_series jsonb,
  run_id        bigint references ideation.run,
  created_at    timestamptz not null default now(),
  constraint experiment_trial_family_config_uq unique (family, config_hash)
);

-- 02 §2.3: distinct nse_bhav_full dates ∪ ^NSEI dates; anchors never fall on a bhav-missing session.
create table if not exists ideation.calendar (
  session      date primary key,
  in_bhav      boolean not null,
  in_index     boolean not null,
  bhav_missing boolean generated always as (not in_bhav) stored,
  run_id       bigint references ideation.run
);

-- 02 §2.2: symbol → security_id → company_id stitching.
create table if not exists ideation.security_map (
  security_id int not null,
  symbol      text not null,
  isin        text not null default '',      -- '' before 2011-06 (no ISIN in the old bhav format)
  valid_from  date not null,
  valid_to    date not null,
  company_id  int,                           -- NULL: no lake company (uncovered / delisted)
  link_method text not null check (link_method in ('seed', 'isin', 'symbol_change', 'contiguity', 'own')),
  run_id      bigint references ideation.run,
  primary key (security_id, symbol, isin, valid_from),
  check (valid_from <= valid_to)
);
create index if not exists security_map_symbol_idx on ideation.security_map (symbol, valid_from);
create index if not exists security_map_company_idx on ideation.security_map (company_id);

-- 02 §2.1: PIT universe per anchor session (+ 03 §C2 mcap provenance).
create table if not exists ideation.universe_member (
  anchor           date not null,
  security_id      int not null,
  symbol           text not null,
  series           text not null,
  isin             text,
  company_id       int,
  adv63_lakh       double precision not null,
  n_days63         int not null,
  rank_adv         int not null,
  mcap_pit_cr      double precision,
  mcap_src         text check (mcap_src in ('A', 'B1', 'B2')),
  mcap_flag        boolean not null default false,
  listed_sessions  int not null,
  scorable         boolean not null,
  exclusion_reason text check (exclusion_reason in
                     ('no_company', 'no_fundamentals_at_anchor', 'mcap_below_floor', 'BE_series', 'too_new')),
  run_id           bigint references ideation.run,
  primary key (anchor, security_id),
  check (scorable = (exclusion_reason is null))
);

-- 02 §2.4: coverage per anchor.
create table if not exists ideation.universe_coverage (
  anchor                    date primary key,
  n_traded                  int not null,
  n_universe                int not null,
  n_scorable                int not null,
  n_no_company              int not null default 0,
  n_no_fundamentals         int not null default 0,
  n_mcap_below_floor        int not null default 0,
  n_be_series               int not null default 0,
  n_too_new                 int not null default 0,
  scorable_share_top500     real,
  scorable_share_top1000    real,
  adv_marginal_lakh         double precision,
  mcap_marginal_cr          double precision,
  extra                     jsonb,
  run_id                    bigint references ideation.run
);

-- 02 §3.4 label variants; 03 §C3 timing: exit = last good session on or before A + h,
-- label_available_from = label_end, purge iff label_end > test anchor. The check forbids an earlier availability.
create table if not exists ideation.label (
  anchor               date not null,
  security_id          int not null,
  horizon              text not null check (horizon in ('1Y', '3M', '1M')),
  entry_date           date not null,        -- s1(A): first session after the anchor
  label_end            date not null,
  label_available_from date not null,
  ret_total            double precision,
  ret_price            double precision,
  excess_univ_med      double precision,
  excess_univ_cw       double precision,
  excess_n500_pr       double precision,
  xs_rank              double precision check (xs_rank between 0 and 1),
  exit_type            text not null check (exit_type in ('normal', 'stale', 'corp_exit', 'distress')),
  stale_days           int,
  flags                text[],
  run_id               bigint references ideation.run,
  primary key (anchor, security_id, horizon),
  check (entry_date > anchor),
  check (label_end >= entry_date),
  check (label_available_from >= label_end)
);
create index if not exists label_end_idx on ideation.label (horizon, label_end);
