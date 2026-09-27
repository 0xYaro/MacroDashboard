-- 20260923000500_btc_box_low_levels.sql
-- Two migrated setups carried levels the journal never wrote.
--
-- The journals write a band's magnitude once, at its end: W32's entry is
-- "$62.0-62.5K" and W34's is "$62.2-62.6K", with a target of "$65-66K". The
-- migrator scaled only the end that carried the K, so both floors were stored
-- as 62 and 62.2, and W34's target as 65. The 65 then made W34's long read as
-- a short, so its invalidation side was inferred as `above` when the journal
-- says "daily close <$62K". Found on /history, where both rows printed
-- "62 - 62,600".
--
-- migration/src/macro_migrate/model.py is fixed and migration/out/40_setup.sql
-- regenerated, so a re-load produces the same rows this file does. Neither
-- setup has a setup_grade row, so no graded outcome or R-multiple moves.
--
-- Idempotent: each update matches only the wrong values.

update macro_core.setup
   set entry_lo = 62000
 where week = '2026-W32'
   and name = 'BTC box-low cluster (reduced size)'
   and entry_lo = 62.0;

update macro_core.setup
   set entry_lo = 62200,
       target = 65000,
       invalidation_dir = 'below'
 where week = '2026-W34'
   and name = 'BTC box-low bid'
   and entry_lo = 62.2;
