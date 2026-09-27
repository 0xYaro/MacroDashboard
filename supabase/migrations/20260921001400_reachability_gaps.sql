-- Three things the web app has to say and cannot, found by auditing its validators
-- against this schema. Each is a claim the operator would otherwise take on trust.

-- ------------------------------------------------------------------ 1 --
-- A one-sided band and a two-sided band missing an edge are different statements and
-- currently render identically: `lo` null with `hi` set means "below X" under a `lt`
-- comparator and "we never wrote the floor" under a `between` one. `text` carries the
-- manual targets, which have no numbers at all and still count toward targets_n.
alter table macro_mart.scenario_target_state
  add column if not exists comparator text null;
alter table macro_mart.scenario_target_state
  add column if not exists text text null;

-- ------------------------------------------------------------------ 2 --
-- A setup's levels are quoted on a specific series and the page could not name it.
-- The worker refuses to score a setup whose series_id is null, so a surface that cannot
-- show the series also cannot explain why a setup is sitting there ungraded.
alter table macro_mart.setup_state
  add column if not exists series_key text null;
alter table macro_mart.setup_state
  add column if not exists series_label text null;

comment on column macro_mart.setup_state.series_key is
  'From macro_core.setup.series_id. Null means the setup names no series and is deliberately not scored.';

-- ------------------------------------------------------------------ 3 --
-- macro_mart.scenario_set_history projects the set, not its three scenarios, so a past
-- week's titles and probabilities were unreadable and /history could only say so.
-- Rendering today's three under a past date would be misattribution, not staleness.
drop view if exists macro_mart.scenario_history;
create view macro_mart.scenario_history
with (security_invoker = false)
as
  select
    sc.id          as scenario_id,
    sc.set_id,
    st.week,
    sc.ordinal,
    sc.title,
    sc.probability,
    sc.positioning,
    st.status,
    st.synthesis,
    st.probability_check,
    st.activated_at          as valid_from,
    successor.activated_at   as valid_to,
    st.superseded_by,
    st.amends,
    st.amend_reason
  from macro_core.scenario sc
  join macro_core.scenario_set st
    on st.id = sc.set_id
  left join macro_core.scenario_set successor
    on successor.id = st.superseded_by;

comment on view macro_mart.scenario_history is
  'Every scenario of every set, including superseded ones, with valid_from and valid_to. A view, never dropped and rebuilt with the evaluated macro_mart tables.';

revoke all on macro_mart.scenario_history from public;
revoke all on macro_mart.scenario_history from anon;
grant select on macro_mart.scenario_history to authenticated;
grant select on macro_mart.scenario_history to service_role;
