-- 061: resolution of expert_call.symbols to securities (security_map) and companies. Live-only.
create table if not exists ideation.expert_call_symbol (
  call_id           text not null references ideation.expert_call on delete cascade,
  symbol            text not null,
  security_id       int,
  company_id        int,
  resolution_method text not null check (resolution_method in ('symbol', 'symbol_latest_segment', 'name', 'unresolved')),
  resolved_at       timestamptz not null default now(),
  primary key (call_id, symbol)
);
create index if not exists expert_call_symbol_company_idx on ideation.expert_call_symbol (company_id);
