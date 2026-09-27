# migration

A one-shot importer.
It reads the weekly market journal history in Ray's Obsidian vault and writes deterministic SQL seed files for `macro_core`.

It is read-only toward the vault and it never touches the live Supabase project.
It emits files; loading them is a separate, deliberate act.

## Run it

```
cd migration
uv sync
uv run macro-migrate build
```

That writes `out/` and regenerates `VERIFICATION.md`, then prints the calibration sentence and the oracle result.
It exits non-zero if any oracle fails.

`VAULT_PATH` overrides the vault location; it defaults to `/Users/rayclaw/Obsidian/rayobs`.

```
uv run pytest
```

Most tests run in milliseconds against the parsed data.
`tests/test_load_postgres.py` creates a throwaway Postgres cluster, applies `supabase/migrations/*.sql`, seeds `macro_core.series` from `config/series.yaml`, loads the emitted seed, loads it a second time to prove replay is safe, and computes the calibration read in SQL.
It skips itself when no local Postgres is installed.

## What it produces

Everything lands in `out/`, in load order.
`out/load.sql` runs them in that order.

| File | What it holds |
| --- | --- |
| `10_judgment.sql` | Phase calls and their four conditions, scenario sets with their scenarios, trigger clauses and target bands, strategies with their sector-focus rows and holdings checks, for 2026-W32 to 2026-W39. |
| `20_week_grade.sql` | The Scorecard Ledger, 2026-W23 to 2026-W38, one row per graded week. |
| `30_scenario_rule.sql` | R1 to R12 and their teaching weeks. |
| `40_setup.sql` | The Setup Ledger from 2026-W35, plus the setups the bootstrapped weeks proposed before the ledger begins. Each setup carries the series its levels are on, read from the setup title; a title that names no instrument, or two, leaves `series_id` null and the flag says which. |
| `50_observation.sql` | The observation history the migrated snapshots carry, across all 47 series in `config/series.yaml`. |
| `60_condition_state.sql` | `macro_core.condition_state_asserted`: the status every migrated manual condition was written with, 56 phase tracker rows and 60 trigger clauses, all `asserted_by = 'migration'`. |
| `series_reconciliation.json` | Which `config/series.yaml` key each journal label was read as, and which keys the migrated history carries no observation for. |
| `manifest.json` | A sha256 per file. |

Every statement is an idempotent upsert on a natural key and every id is a uuid5 over that key, so a second run is byte-identical and a second load is a no-op.
A phase call and its conditions, and a scenario set and its scenarios, are each written inside one transaction, because the cardinality rules in `supabase/migrations/20260921000500_core_constraints.sql` are deferred constraint triggers that fire at `COMMIT`.

Each file that references a series opens with a preflight block that fails the load loudly if `macro_core.series` is missing a key the seed uses.
An unreconciled key can never load as a silent null.

## What it reads

| Source | Used for |
| --- | --- |
| `Scorecard Ledger.md` | `week_grade`, 2026-W23 onward, read through the vault's own `calibration.py` parser so the rows and the calibration sentence can never drift apart. |
| `Setup Ledger.md` | `setup` and `setup_grade`, 2026-W35 onward. |
| `scenario-rules.md` | `scenario_rule` and `scenario_rule_week`. |
| `data/2026-W3*.json` | The judgment tables and the observation history, for 2026-W36 to 2026-W39. |
| `data/2026-W3*.points.json` | Rows the merged sidecar dropped, which is where `ig_oas_bps` comes from. |
| `2026-W32.md`, `2026-W34.md`, `2026-W35.md` | Bootstrapped through `vendor/journal_parse.py --legacy`. |
| `config/series.yaml` | The one source of series keys, units and value kinds. |

`vendor/` holds unmodified copies of the vault's `journal_parse.py` and `calibration.py`.
They are vendored rather than imported so this project has no dependency on a path outside the repository, and they are never edited: the one repair W32 needs is applied to the markdown handed to the parser, not to the parser.

## What it deliberately leaves out

- **The 2026-W03 to 2026-W31 archive.** `docs/DECISIONS.md` says not to import it. Those weeks exist as `week_grade` rows only, which is what the calibration read needs.
- **A grading date for those nine weeks.** They were graded by journals in that archive, so no authoring date is known and `graded_at` is null. The Monday of the following ISO week would have been a manufactured fact.
- **A per-clause verdict on any migrated trigger.** The grading journals judge a trigger as a whole, so every migrated clause is asserted `unevaluable` and carries that whole-trigger verdict as its `current_text`. Phase tracker rows do carry a per-condition status, and keep it.
- **2026-W33.** There is no journal. The Scorecard Ledger records that the 2026-W32 scenario set was graded over two weeks.
- **`macro_core.event`, `catalyst`, `holding`, `watchlist_change` and `tail_risk`.** The sidecars carry an event log and a catalyst table, but the worker re-parses those from the three OpenClaw vault files on every run. Seeding them here would create a second writer for rows that are re-derived twice a day.
- **Every `macro_mart` table.** `macro_mart` is recomputed by the worker's evaluate step. This migration writes no derived row.
- **`macro_core.series`.** `config/series.yaml` is its one source and the worker applies it.
- **Typed condition expressions.** Every migrated phase condition and trigger clause is `kind = 'manual'` with a null `expr`. The journals write conditions as prose, and inventing an expression for one would put an unverifiable machine check on a historical judgment. The weekly authoring run adds them going forward.
- **Four snapshot rows `config/series.yaml` does not carry:** NAAIM Exposure, Coinbase Premium / CME basis, Alt OI excluding BTC and ETH, and 7-day Liquidations. Each was dropped from the journal during the migrated window. `VERIFICATION.md` names them and the weeks they appeared in.

## The two oracles

**The calibration reproduction.**
The 2026-W39 journal's section 0 prints a sentence computed by the vault's `calibration.py`.
The migrated `week_grade` rows, run through the same arithmetic over the database columns, must reproduce it character for character.
They do.

**The recorded target denominators.**
`docs/CONTRACT.md` says the `scenario_target` row count is the `n` in `targets hit x of n`.
Every grading journal records that `n` per scenario, so nine recorded denominators stand as a test oracle.
Eight match.
The ninth, 2026-W36's S2, is a defect in the source: the same grading block counted W36's S1 on the opposite convention, and both cannot be right.
`VERIFICATION.md` sets it out in full.

## Verification

`VERIFICATION.md` is regenerated with the seed files, so it cannot drift from them.
It states, for every migrated week, what was read, what was produced, and every field the source did not carry.
It also lists every disagreement found between `docs/CONTRACT.md`, `supabase/migrations/*.sql` and the sources.
