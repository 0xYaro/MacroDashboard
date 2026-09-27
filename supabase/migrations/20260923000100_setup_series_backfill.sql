-- The four 2026-W39 setups carry no series_id, so nothing says which series their
-- entry, invalidation and target are levels of. worker/src/macro/store.py opens
-- `setup_series_missing` at severity 1 for each of them and refuses to score them:
-- no entry state, no R-multiple, no setup_state row and no Setup Ledger line.
--
-- The cause was in the migration rather than in the data. migration/ never emitted
-- the column at all, so every migrated setup loaded with a null series_id and the
-- four live ones are simply the four that are still active. The migrator now reads
-- the instrument from the setup title and emits series_id with the rest of the row,
-- so a re-load of migration/out/40_setup.sql sets the same four ids this file does.
-- This file exists so the live rows are correct without waiting for that re-load.
--
-- An equity setup is quoted against the cash index and never the future. Every phase
-- condition, scenario band and setup level in the journal is written against the cash
-- index, and R1 exists because the two were once conflated in the week-over-week
-- arithmetic. A crypto setup is quoted against the spot coin.
--
-- Idempotent twice over: it matches on (week, asset, name) and only where series_id
-- is still null, so a second run updates nothing and an authoring run that has since
-- set a different series is not overwritten.

-- The preflight is conditional because a migration runs before anything is seeded.
-- On a clean database macro_core.setup and macro_core.series are both empty and this
-- file has nothing to do, so demanding the keys there would fail every fresh build.
-- Where the setups do exist, a missing key is loud: config/series.yaml is
-- authoritative for series keys and the worker seeds macro_core.series from it, so a
-- renamed key would leave the join below matching nothing and the backfill reporting
-- success having done nothing, which is indistinguishable from a setup that names no
-- series.
DO $$
DECLARE missing text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM macro_core.setup WHERE week = '2026-W39'
  ) THEN
    RAISE NOTICE 'no 2026-W39 setups are present; nothing to backfill';
    RETURN;
  END IF;

  SELECT string_agg(k, ', ' ORDER BY k) INTO missing
  FROM (SELECT unnest(ARRAY['btc', 'eth', 'nasdaq_cash', 'spx_cash']) AS k) w
  WHERE NOT EXISTS (SELECT 1 FROM macro_core.series s WHERE s.key = w.k);
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'macro_core.series is missing keys this backfill needs: %', missing;
  END IF;

  UPDATE macro_core.setup s
     SET series_id = sr.id
    FROM (VALUES
            ('2026-W39', 'crypto',   'BTC $82K acceptance retest (FTR, HTF)', 'btc'),
            ('2026-W39', 'crypto',   'ETH breakout-shelf FTR (FTR, HTF)',     'eth'),
            ('2026-W39', 'equities', 'Nasdaq failed-push fade (O/U, HTF)',    'nasdaq_cash'),
            ('2026-W39', 'equities', 'S&P FOMC-shelf FTR long (FTR, HTF)',    'spx_cash')
         ) AS want (week, asset, name, series_key)
    JOIN macro_core.series sr ON sr.key = want.series_key
   WHERE s.week = want.week
     AND s.asset = want.asset
     AND s.name = want.name
     AND s.series_id IS NULL;
END $$;
