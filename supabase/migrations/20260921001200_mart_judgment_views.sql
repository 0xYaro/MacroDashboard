-- 20260921001200_mart_judgment_views.sql
-- "Judgment projected onto the Data API", per docs/CONTRACT.md.
--
-- macro_core is not exposed and the three routes have to render judgment, not only
-- arithmetic. Each object below is a read-only view over macro_core, not a
-- recomputation, and like the two history views in 0007 they are never dropped and
-- rebuilt the way the evaluated macro_mart tables are.
--
-- Security model, identical to 0007 and to Accounting's core -> mart mirror:
-- every view is created WITH (security_invoker = false), so it runs with the privileges
-- of its owner (the migration role, which owns the macro_core tables) rather than the
-- querying role's. authenticated has no USAGE on schema macro_core and no grant on any
-- table in it, so security_invoker = true would make every query fail with
-- "permission denied for schema macro_core" whatever was granted on the view itself.
-- The bypass stays narrow: these views select named columns, they grant SELECT only,
-- and together with the two in 0007 they are the only objects reading macro_core from
-- outside the service-role key.
--
-- Each view grants itself. 0009 runs before this file, so its
-- "grant select on all tables in schema macro_mart" cannot reach these.
--
-- Validity intervals in the two history views below follow 0007 exactly:
--   valid_from  activated_at, null while the row is still only proposed
--   valid_to    the activated_at of the row that superseded this one, read through
--               superseded_by rather than guessed from the next row by authored_at
--
-- Re-runnable: CREATE OR REPLACE VIEW and GRANT/REVOKE are idempotent.
--
-- CREATE OR REPLACE VIEW cannot change an existing view's column list, so each view is
-- dropped first. Dropping and recreating a view inside one transaction is invisible to
-- readers; this is not the "dropped and rebuilt" the contract forbids, which is about
-- discarding the rows a recomputation would have to re-derive. A view has no rows.

-- ============================================================
-- Active judgment
-- ============================================================

-- Without this the Phase view shows a score with no argument behind it, and a falsifier
-- the operator cannot read cannot be checked.
-- asset is the primary key because macro_core.phase_call carries a partial unique index
-- on (asset) where status = 'active', so at most one row per asset can reach this view.
drop view if exists macro_mart.phase_call_detail;
create view macro_mart.phase_call_detail
with (security_invoker = false)
as
  select
    p.asset,
    p.id          as phase_call_id,
    p.phase,
    p.phase_name,
    p.prior_phase,
    p.direction,
    p.reason,
    p.evidence,
    p.falsifier,
    p.week,
    p.activated_at
  from macro_core.phase_call p
  where p.status = 'active';

comment on view macro_mart.phase_call_detail is
  'The standing phase call per asset, with the argument behind it. Active rows only; superseded ones are read through macro_mart.phase_call_history.';

-- Without this a scenario set is three numbers with no case for them.
drop view if exists macro_mart.scenario_set_state;
create view macro_mart.scenario_set_state
with (security_invoker = false)
as
  select
    s.id  as set_id,
    s.week,
    s.synthesis,
    s.probability_check,
    s.swing_variables,
    s.rules_waived,
    s.activated_at
  from macro_core.scenario_set s
  where s.status = 'active';

comment on view macro_mart.scenario_set_state is
  'The standing scenario set and the case for it. Active rows only; superseded ones are read through macro_mart.scenario_set_history.';

-- The child rows are aggregated into jsonb so that one select answers the Setups view.
-- sector_focus is ordered CRITICAL, HOLD, WATCH, AVOID, which is the order the journal
-- writes it in and is not alphabetical. holdings_check carries sector_focus_id rather
-- than a resolved band, because the sector_focus array on the same row already resolves
-- it and duplicating the band would give a reader two places to disagree.
drop view if exists macro_mart.strategy_state;
create view macro_mart.strategy_state
with (security_invoker = false)
as
  select
    st.id  as strategy_id,
    st.week,
    st.asset,
    st.stance,
    st.posture,
    st.exposure_lo,
    st.exposure_hi,
    st.exposure_operated_lo,
    st.exposure_operated_hi,
    coalesce(sf.rows, '[]'::jsonb)  as sector_focus,
    coalesce(hc.rows, '[]'::jsonb)  as holdings_check,
    st.activated_at
  from macro_core.strategy st
  left join lateral (
    select jsonb_agg(
             jsonb_build_object('id', f.id, 'band', f.band, 'text', f.text)
             order by case f.band
                        when 'CRITICAL' then 1
                        when 'HOLD'     then 2
                        when 'WATCH'    then 3
                        else 4
                      end
           ) as rows
    from macro_core.sector_focus f
    where f.strategy_id = st.id
  ) sf on true
  left join lateral (
    select jsonb_agg(
             jsonb_build_object(
               'id', h.id,
               'ticker', h.ticker,
               'action', h.action,
               'text', h.text,
               'sector_focus_id', h.sector_focus_id
             )
             order by h.ticker
           ) as rows
    from macro_core.holdings_check h
    where h.strategy_id = st.id
  ) hc on true
  where st.status = 'active';

comment on view macro_mart.strategy_state is
  'The standing strategy per book, with its sector focus and holdings check aggregated into jsonb so one select answers the Setups view. Active rows only.';

-- ============================================================
-- The two ledgers
-- ============================================================

-- /calibration is defined as rendering both ledgers.
drop view if exists macro_mart.week_grade;
create view macro_mart.week_grade
with (security_invoker = false)
as
  select
    g.week,
    g.base_scenario_id,
    g.base_scenario_label,
    g.base_grade,
    g.other_grades,
    g.eq_call,
    g.crypto_call,
    g.key_miss,
    g.targets_hit,
    g.targets_n,
    g.brier,
    g.premise_moved,
    g.graded_at
  from macro_core.week_grade g;

comment on view macro_mart.week_grade is
  'The Scorecard Ledger. graded_at is null for the migrated weeks, which were graded by archive journals this system deliberately does not read; a derived grading date would be a manufactured fact. other_grades is jsonb of shape [{"id":"S1","prob":30,"grade":"Not","note":"..."}].';

-- Joined to its setup so the ledger can be sorted and read the way the markdown one is.
-- The contract names proposed_week, asset and name. The four level columns are carried
-- too: the markdown Setup Ledger prints Entry, Invalidation, Target and R:R planned in
-- its own table, macro_core is not on the Data API, and a view that forces a second
-- query into macro_core to fill four of its own columns cannot be read at all.
-- See the final report: this is an addition beyond the contract's enumerated list.
drop view if exists macro_mart.setup_grade;
create view macro_mart.setup_grade
with (security_invoker = false)
as
  select
    sg.setup_id,
    s.week   as proposed_week,
    s.asset,
    s.name,
    s.style,
    s.entry_lo,
    s.entry_hi,
    s.invalidation,
    s.invalidation_dir,
    s.target,
    s.rr_planned,
    sg.graded_week,
    sg.outcome,
    sg.r_realised,
    sg.note
  from macro_core.setup_grade sg
  join macro_core.setup s on s.id = sg.setup_id;

comment on view macro_mart.setup_grade is
  'The Setup Ledger, joined to the setup it grades so proposed_week, asset, name and the planned levels travel with the outcome. An open row is rewritten the following week, never duplicated.';

-- fire_count is the cardinality of teaching_weeks, so the two can never disagree.
-- cardinality returns 0 for an empty array where array_length returns null, which is why
-- it is used here: a rule that has never fired reports 0, not null.
drop view if exists macro_mart.scenario_rule;
create view macro_mart.scenario_rule
with (security_invoker = false)
as
  select
    r.number,
    r.text,
    r.retired_week,
    coalesce(w.weeks, '{}'::text[])              as teaching_weeks,
    cardinality(coalesce(w.weeks, '{}'::text[])) as fire_count
  from macro_core.scenario_rule r
  left join lateral (
    select array_agg(rw.week order by rw.week) as weeks
    from macro_core.scenario_rule_week rw
    where rw.rule_number = r.number
  ) w on true;

comment on view macro_mart.scenario_rule is
  'R1 to R12 with their teaching weeks and fire counts. Numbers are never reused and a retired rule is kept with its retired_week set. fire_count is the cardinality of teaching_weeks, so the count and the list can never disagree.';

-- ============================================================
-- History
-- ============================================================

-- /history renders the page as it stood on a past date, and the Snapshot view cannot do
-- that from current-state rows alone. unit comes from macro_core.series, which is where
-- the one canonical unit per series is declared; an observation does not carry its own.
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
    o.observed_at
  from macro_core.observation o
  join macro_core.series s on s.id = o.series_id;

comment on view macro_mart.observation_history is
  'Every observation ever recorded, keyed (series_id, asof), so /history can render the Snapshot as it stood on a past date. unit is carried from macro_core.series, where the one canonical unit per series is declared.';

-- The same shape as macro_mart.strategy_state, including superseded rows, with
-- valid_from and valid_to. The lifecycle columns travel with it for the same reason they
-- travel with the two views in 0007: /history has to be able to say what was believed,
-- when it stopped being believed, and whether it was an amendment.
drop view if exists macro_mart.strategy_history;
create view macro_mart.strategy_history
with (security_invoker = false)
as
  select
    st.id  as strategy_id,
    st.week,
    st.asset,
    st.stance,
    st.posture,
    st.exposure_lo,
    st.exposure_hi,
    st.exposure_operated_lo,
    st.exposure_operated_hi,
    coalesce(sf.rows, '[]'::jsonb)  as sector_focus,
    coalesce(hc.rows, '[]'::jsonb)  as holdings_check,
    st.status,
    st.authored_at,
    st.activated_at          as valid_from,
    successor.activated_at   as valid_to,
    st.superseded_by,
    st.amends,
    st.amend_reason
  from macro_core.strategy st
  left join macro_core.strategy successor
    on successor.id = st.superseded_by
  left join lateral (
    select jsonb_agg(
             jsonb_build_object('id', f.id, 'band', f.band, 'text', f.text)
             order by case f.band
                        when 'CRITICAL' then 1
                        when 'HOLD'     then 2
                        when 'WATCH'    then 3
                        else 4
                      end
           ) as rows
    from macro_core.sector_focus f
    where f.strategy_id = st.id
  ) sf on true
  left join lateral (
    select jsonb_agg(
             jsonb_build_object(
               'id', h.id,
               'ticker', h.ticker,
               'action', h.action,
               'text', h.text,
               'sector_focus_id', h.sector_focus_id
             )
             order by h.ticker
           ) as rows
    from macro_core.holdings_check h
    where h.strategy_id = st.id
  ) hc on true;

comment on view macro_mart.strategy_history is
  'macro_mart.strategy_state including superseded rows, with valid_from and valid_to. Never dropped and rebuilt with the evaluated macro_mart tables.';

-- The authored shape of a setup, including superseded rows, with valid_from and
-- valid_to. It carries sector_focus_label rather than sector_focus_id alone, matching
-- macro_mart.setup_state, because macro_core.sector_focus is not on the Data API.
-- The evaluated columns of setup_state (gate_state, entry_state, band_touched_*,
-- sessions_carried, r_running) are absent here: they are recomputed from the tape on
-- every run and were never stored per version, so there is no past value of them to
-- project. See the final report.
drop view if exists macro_mart.setup_history;
create view macro_mart.setup_history
with (security_invoker = false)
as
  select
    s.id  as setup_id,
    s.week,
    s.asset,
    s.name,
    s.style,
    s.entry_lo,
    s.entry_hi,
    s.invalidation,
    s.invalidation_dir,
    s.target,
    s.rr_planned,
    s.gate_text,
    s.gate_expr,
    s.sector_focus_id,
    f.text  as sector_focus_label,
    s.rationale,
    s.status,
    s.authored_at,
    s.activated_at          as valid_from,
    successor.activated_at  as valid_to,
    s.superseded_by,
    s.amends,
    s.amend_reason
  from macro_core.setup s
  left join macro_core.setup successor
    on successor.id = s.superseded_by
  left join macro_core.sector_focus f
    on f.id = s.sector_focus_id;

comment on view macro_mart.setup_history is
  'The authored shape of macro_mart.setup_state including superseded rows, with valid_from and valid_to. Never dropped and rebuilt with the evaluated macro_mart tables.';

-- ============================================================
-- Grants
-- ============================================================
-- 0009 runs before this file, so these nine views are granted here rather than by its
-- "all tables in schema" grant. anon gets nothing, here as everywhere.

do $$
declare
  v text;
begin
  foreach v in array array[
    'phase_call_detail',
    'scenario_set_state',
    'strategy_state',
    'week_grade',
    'setup_grade',
    'scenario_rule',
    'observation_history',
    'strategy_history',
    'setup_history'
  ] loop
    execute format('revoke all on macro_mart.%I from public, anon', v);
    execute format('grant select on macro_mart.%I to authenticated', v);
    execute format('grant select on macro_mart.%I to service_role', v);
  end loop;
end $$;
