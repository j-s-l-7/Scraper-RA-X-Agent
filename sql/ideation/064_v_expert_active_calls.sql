-- 064: idea-level expert calls with their resolution (company_id NULL = unresolved or macro/theme). The overlay reads
-- this; never a backtest path. Live-only.
create or replace view ideation.v_expert_active_calls as
select c.call_id, c.author, c.posted_at, c.kind, c.subject, c.subject_type, c.thesis, c.direction, c.horizon,
       c.confidence, s.symbol, s.security_id, s.company_id, s.resolution_method
from ideation.expert_call c
left join ideation.expert_call_symbol s using (call_id)
where c.kind = 'idea';
