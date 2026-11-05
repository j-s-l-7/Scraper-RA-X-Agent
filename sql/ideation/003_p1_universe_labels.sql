-- 003: Phase 1 (05 §1 universes, §8 holdout; 03 §C2 mcap, §C3 label timing; 02 §2.2 stitching, §3.3 exits).
-- Idempotent without DO blocks (writer.guard forbids them): columns use ADD COLUMN IF NOT EXISTS, and every
-- constraint this file owns is dropped (IF EXISTS) and re-added in the same ALTER, so a second application is a no-op
-- on the catalog. 002's auto-named checks are replaced here, never edited in place.

-- ── security_map (02 §2.2): link provenance of the company mapping, separate from the segment link ──────────────
-- link_method (002) says how a (symbol, isin) segment joined its security; company_link says how the security got
-- its company_id: by ISIN (company.isin), by the current nse_symbol of its latest segment, or none (uncovered).
alter table ideation.security_map
  add column if not exists company_link text,
  add column if not exists series_last  text;
alter table ideation.security_map
  drop constraint if exists security_map_company_link_check,
  add constraint security_map_company_link_check check (company_link in ('isin', 'symbol', 'none'));

-- ── universe_member (05 §1 / O1 / O10): several universes per anchor, one row per EQ/BE security ──────────────────
-- A row exists for every stitched EQ/BE security that traded at least once in the 63-session window at the anchor.
-- scorable/exclusion_reason (002) now mean U_score, the 1Y training/scoring universe (05 §1.1, ADV63 >= ₹0.2 cr).
-- The ₹0.5 cr sensitivity, U_score_T (05 §1.2) and the 03 §C4 top-1000 slice (in_u1000) are separate booleans.
alter table ideation.universe_member
  add column if not exists traded_ok          boolean not null default false,  -- >= 50/63 sessions and in last 5
  add column if not exists last_trade         date,
  add column if not exists in_u1000           boolean not null default false,
  add column if not exists in_u500            boolean not null default false,
  add column if not exists in_u_score         boolean not null default false,
  add column if not exists in_u_score_adv05   boolean not null default false,
  add column if not exists in_u_score_t       boolean not null default false,
  add column if not exists u_score_t_reason   text,
  add column if not exists has_fy_at_anchor   boolean not null default false,  -- an annual FY is available at A
  add column if not exists mcap_a_cr          double precision,
  add column if not exists mcap_b1_cr         double precision,
  add column if not exists mcap_b2_cr         double precision,
  add column if not exists feature_complete   real;                            -- share of core features; P2 fills
alter table ideation.universe_member
  drop constraint if exists universe_member_exclusion_reason_check,
  add constraint universe_member_exclusion_reason_check check (exclusion_reason in (
    'thin_trading', 'adv_below_floor', 'too_new', 'no_company', 'no_fundamentals_at_anchor', 'mcap_unknown',
    'mcap_below_floor_100', 'features_incomplete', 'mcap_below_floor', 'BE_series')),
  drop constraint if exists universe_member_u_score_t_reason_check,
  add constraint universe_member_u_score_t_reason_check check (u_score_t_reason in (
    'thin_trading', 'adv_below_floor', 'too_new', 'mcap_below_floor_100', 'adv_below_floor_unknown_mcap')),
  drop constraint if exists universe_member_scorable_is_u_score_check,
  add constraint universe_member_scorable_is_u_score_check check (scorable = in_u_score),
  drop constraint if exists universe_member_u_score_t_check,
  add constraint universe_member_u_score_t_check check (in_u_score_t = (u_score_t_reason is null)),
  drop constraint if exists universe_member_u_score_subsets_check,
  add constraint universe_member_u_score_subsets_check check (
    (not in_u_score_adv05 or in_u_score) and (not in_u500 or in_u1000) and (not in_u1000 or traded_ok)
    and (not in_u_score or traded_ok) and (not in_u_score_t or traded_ok));
create index if not exists universe_member_u_score_idx on ideation.universe_member (anchor) where in_u_score;

-- ── universe_coverage (02 §2.4 + 05 §1): N per universe and the new exclusion reasons ────────────────────────────
alter table ideation.universe_coverage
  add column if not exists n_u_score              int,
  add column if not exists n_u_score_adv05        int,
  add column if not exists n_u_score_t            int,
  add column if not exists n_u1000                int,
  add column if not exists n_thin_trading         int not null default 0,
  add column if not exists n_adv_below_floor      int not null default 0,
  add column if not exists n_mcap_unknown         int not null default 0,
  add column if not exists n_mcap_below_floor_100 int not null default 0;

-- ── label (02 §3.3–3.4, 03 §C3): exit provenance and the last_price sensitivity (03 §A9) ────────────────────────
alter table ideation.label
  add column if not exists entry_price          double precision,  -- NULL when the name never traded after A
  add column if not exists exit_price           double precision,
  add column if not exists last_trade           date,              -- last trade used for the exit, <= label_end
  add column if not exists ret_total_last_price double precision;  -- distress exits at last traded close

-- ── holdout seal (05 §8): one row per family, written once by `ideation holdout unseal` (E1's CLI) ─────────────────
create table if not exists ideation.holdout_unseal (
  family               text primary key,
  champion_trial_id    bigint,
  champion_config_hash text not null,
  baseline_config_hash text,
  note                 text,
  unsealed_at          timestamptz not null default now(),
  run_id               bigint references ideation.run
);
