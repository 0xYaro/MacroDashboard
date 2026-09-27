-- Both State Street series display the same blob:
--   "57.4% (Aug) (-0.13 pp MoM); Risk Appetite Index +0.36 (Jul +0.18)"
--
-- The journal prints the equity allocation and the Risk Appetite Index in one
-- snapshot cell, and migration/ handed that whole cell to both series. So the
-- allocation row states an index reading it has no business stating, and the index
-- row leads with a percentage that is not its own figure at all. config/series.yaml
-- keeps them apart precisely because one is a percentage of assets and the other a
-- standardised index, and a display that states both is the conflation the split was
-- made to prevent.
--
-- migration/src/macro_migrate/model.py now cuts the cell at its top-level semicolons
-- before anything reads it, so a re-load of migration/out/50_observation.sql produces
-- the same text this file does. This file exists so the live rows are correct without
-- waiting for that re-load.
--
-- Idempotent: the predicate is "the display still names the Risk Appetite Index", so
-- a second run matches nothing.

-- The index keeps its own clause, with the lead-in cut off: the series label already
-- says "State Street Risk Appetite Index", so repeating it inside the value is noise.
update macro_core.observation o
   set display = btrim((regexp_match(
         o.display, '(?:^|;[[:space:]]*)Risk Appetite(?:[[:space:]]+Index)?[[:space:]]*:?[[:space:]]*([^;]*)'
       ))[1])
  from macro_core.series s
 where s.id = o.series_id
   and s.key = 'state_street_risk_appetite'
   and o.display ~* 'Risk Appetite'
   and (regexp_match(
         o.display, '(?:^|;[[:space:]]*)Risk Appetite(?:[[:space:]]+Index)?[[:space:]]*:?[[:space:]]*([^;]*)'
       ))[1] is not null;

-- The allocation loses that one clause and keeps everything else it said. The
-- component split ("FI 24.9%, cash 17.6%") is the allocation's own breakdown and
-- stays with it.
update macro_core.observation o
   set display = btrim(regexp_replace(
         o.display, ';[[:space:]]*Risk Appetite(?:[[:space:]]+Index)?[[:space:]]*:?[[:space:]]*[^;]*', '', 'gi'
       ))
  from macro_core.series s
 where s.id = o.series_id
   and s.key = 'state_street_equity_allocation'
   and o.display ~* ';[[:space:]]*Risk Appetite';

-- macro_mart.snapshot_row is derived: the worker's evaluate step rebuilds it from
-- macro_core.observation on every run and would repair this on its own. It is
-- repaired here as well so the screen is right before the next run rather than after
-- it, and the arithmetic is the same either way.
update macro_mart.snapshot_row r
   set display = o.display,
       updated_at = now()
  from macro_core.observation o
 where o.series_id = r.series_id
   and o.asof = r.asof
   and r.key in ('state_street_equity_allocation', 'state_street_risk_appetite')
   and r.display is distinct from o.display;
