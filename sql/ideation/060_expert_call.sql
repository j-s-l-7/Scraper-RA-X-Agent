-- 060: expert calls from X (04 expert-signals addendum). LIVE-ONLY: on the backtest ban list, never a feature, label or
-- training input. One row per extracted call. The tweet text is NEVER stored: only its sha256 (text_hash), the model's
-- structured output and a short thesis summary. Idempotent.
create table if not exists ideation.expert_call (
  call_id        text primary key,            -- '<tweet_id>:<n>'
  tweet_id       bigint not null,
  author         text not null,               -- X handle
  posted_at      timestamptz not null,
  text_hash      text not null,               -- sha256(text + parent context); the text itself is not stored
  kind           text not null check (kind in ('idea', 'principle')),
  subject        text,
  subject_type   text check (subject_type in ('company', 'sector', 'theme', 'macro', 'index', 'asset')),
  thesis         text,                        -- model-written summary, capped; not the raw post
  symbols        text[] not null default '{}',
  direction      text check (direction in ('bullish', 'bearish', 'avoid', 'watch')),
  horizon        text,
  confidence     text check (confidence in ('low', 'medium', 'high')),
  model          text,
  prompt_version text,
  created_at     timestamptz not null default now()
);
create index if not exists expert_call_author_idx on ideation.expert_call (author, posted_at);
create index if not exists expert_call_hash_idx on ideation.expert_call (text_hash);
