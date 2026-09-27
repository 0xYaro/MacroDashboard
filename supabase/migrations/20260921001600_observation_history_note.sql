-- macro_mart.observation_history omitted macro_core.observation.note, which is where each
-- observation's one-line interpretation lives: the journal's Read cell, written when the
-- reading was taken.
--
-- The Snapshot disclosure is meant to show a series' interpretation over time, and without
-- this it could show only the current read beside a column of bare historical values. The
-- alternative, generating a read today from an old number, would be the app manufacturing a
-- judgment nobody made, which is the one thing every honesty rule in this system exists to
-- prevent. A blank read on an older observation is the correct answer where none was written.
drop view if exists macro_mart.observation_history;
create view macro_mart.observation_history
with (security_invoker = false)
as
  select
    o.series_id,
    s.key   as series_key,
    o.asof,
    o.value,
    o.display,
    s.unit,
    o.tier,
    o.source,
    o.released,
    o.observed_at,
    o.note
  from macro_core.observation o
  join macro_core.series s on s.id = o.series_id;

comment on view macro_mart.observation_history is
  'Every observation ever recorded, keyed (series_id, asof), so /history can render the Snapshot as it stood on a past date and a Snapshot row can show its interpretation over time. unit is carried from macro_core.series, where the one canonical unit per series is declared. note is the read as written at the time, and is null where none was.';

revoke all on macro_mart.observation_history from public;
revoke all on macro_mart.observation_history from anon;
grant select on macro_mart.observation_history to authenticated;
grant select on macro_mart.observation_history to service_role;
