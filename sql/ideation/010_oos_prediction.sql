-- 010 (E1, 05 §2): out-of-sample predictions, one row per (trial, anchor, security). The single source for consensus,
-- nested calibration and outcomes. Large sets also go to parquet under IDEATION_DATA_DIR/oos/<trial_id>.parquet;
-- this table is the index. Idempotent.
create table if not exists ideation.oos_prediction (
  trial_id        bigint not null references ideation.experiment_trial,
  anchor          date not null,
  security_id     int not null,
  score           real,
  p               real check (p > 0 and p < 1),          -- (rank − 0.5) / n within U_score at the anchor
  bucket_dec      smallint check (bucket_dec between 1 and 10),
  label           real,                                  -- the training target (rank-gauss of ret_total)
  ret_total       real,
  excess_univ_med real,
  fold            text,
  primary key (trial_id, anchor, security_id)
);
create index if not exists oos_prediction_anchor_idx on ideation.oos_prediction (anchor, trial_id);
