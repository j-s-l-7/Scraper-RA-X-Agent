-- 012 (E1 DDL for MS1-text's outcomes module, 05 §5 / §10.2.c): post-anchor "exceeds expectations" outcomes. These
-- read labels and future facts by design, are never features, and only ideation/outcomes writes them. Idempotent.
create table if not exists ideation.outcome (
  anchor       date not null,
  security_id  int not null,
  horizon      text not null check (horizon in ('1Y', '3M', '1M')),
  kind         text not null check (kind in ('analyst', 'fundamental', 'fundamental_q')),
  n_events     int,
  out_beat     real check (out_beat between 0 and 1),
  out_surp     real check (out_surp between -2 and 2),
  out_fs_sales real,
  out_fs_pat   real,
  out_accel    smallint check (out_accel in (0, 1)),
  extra        jsonb,
  run_id       bigint references ideation.run,
  primary key (anchor, security_id, horizon, kind)
);
