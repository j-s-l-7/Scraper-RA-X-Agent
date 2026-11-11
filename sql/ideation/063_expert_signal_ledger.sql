-- 063: forward-return ledger. Entry = close of the first NSE session strictly after posted_at (IST date). Forward
-- returns are filled only once the horizon session exists. Live-only; the record starts at the configured date.
create table if not exists ideation.expert_signal_ledger (
  call_id      text not null references ideation.expert_call on delete cascade,
  security_id  int not null,
  company_id   int,
  author       text not null,
  direction    text not null,
  posted_at    timestamptz not null,
  entry_date   date,
  entry_px     double precision,
  ret_1m       double precision,
  ret_3m       double precision,
  ret_12m      double precision,
  bench_1m     double precision,              -- Nifty 500 proxy return over the same window (NULL until available)
  bench_3m     double precision,
  bench_12m    double precision,
  updated_at   timestamptz not null default now(),
  primary key (call_id, security_id)
);
create index if not exists expert_signal_ledger_author_idx on ideation.expert_signal_ledger (author, posted_at);
