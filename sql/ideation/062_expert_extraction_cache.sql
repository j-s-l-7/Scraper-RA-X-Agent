-- 062: extraction cache keyed by text_hash (+ prompt version + model). A rerun never pays twice for the same post.
-- Holds the model's structured output (no raw tweet text). Live-only.
create table if not exists ideation.expert_extraction_cache (
  cache_key      text primary key,            -- sha256(text_hash | prompt_version | model)
  text_hash      text not null,
  prompt_version text not null,
  model          text not null,
  result         jsonb not null,              -- {"items": [...]} as returned by the schema-validated extraction
  tokens_in      int,
  tokens_out     int,
  created_at     timestamptz not null default now()
);
create index if not exists expert_extraction_cache_hash_idx on ideation.expert_extraction_cache (text_hash);
