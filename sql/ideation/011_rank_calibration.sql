-- 011 (E1, 05 §2 R5/R6): calibrated expected excess and conformal band per bucket, per test anchor. 'nested' uses
-- only counted anchors < T whose labels ended by T (decision grade); 'pooled' uses every unsealed OOS anchor
-- (descriptive). model_key = experiment_trial.config_hash[:12]. Idempotent.
create table if not exists ideation.rank_calibration (
  model_key    text not null,
  horizon      text not null check (horizon in ('1Y', '3M', '1M')),
  test_anchor  date not null,
  scheme       text not null check (scheme in ('nested', 'pooled')),
  bucket       text not null check (bucket in ('P99', 'P95', 'D10', 'D9', 'D8', 'D7', 'D6', 'D5', 'D4', 'D3', 'D2',
                                               'D1', 'P5', 'P1')),
  n            int,
  alpha_raw    real,
  alpha_shrunk real,
  alpha_iso    real,
  conf_lo      real,
  conf_hi      real,
  coverage     real check (coverage between 0 and 1),
  run_id       bigint references ideation.run,
  primary key (model_key, horizon, test_anchor, scheme, bucket)
);
