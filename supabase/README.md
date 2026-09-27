# supabase/

This directory holds the schema, constraints, RLS and seed for the macro dashboard.
It is applied to the shared `GreenfieldFund` Supabase project.
Read `docs/CONTRACT.md` first: it is binding, and this README only covers how to apply what it describes.

## This project is shared with Accounting

The same Postgres database already holds Accounting's `raw`, `core`, `mart` and `quality` schemas.
This system owns `macro_raw`, `macro_core`, `macro_mart` and `macro_quality` and nothing else.
Every statement in `migrations/` names those four explicitly and every object reference is fully qualified.
No migration here reads, writes, grants on, revokes from or drops anything in Accounting's four schemas, and `tests/smoke.sql` checks that after the fact.

One read crosses the line, at run time and never in a migration: `20260924000200_desk_role.sql` defines `macro_core.accounting_open_positions()` and `macro_core.accounting_venue_status()`, `SECURITY DEFINER` functions whose dynamic SQL reads `mart.book_position`, `mart.venue_status` and `core.instrument` for the holdings watch.
They add no grant or policy on Accounting and record no dependency on its columns, so Accounting's migrations are never blocked by them; `tests/migrate_test.py` scenario 10 asserts both.

If you ever add a migration to this directory, the rule is the same: name the schema, never loop over `pg_tables` without a `schemaname in ('macro_...')` filter, and never write an unqualified `alter default privileges` that would land in `public`.

## Why `supabase db push` is never used here

Supabase records applied migrations in `supabase_migrations.schema_migrations`.
That table is scoped to the **database**, not to the schema, so in a shared project there is exactly one of it and Accounting owns rows in it.

The two products named their migrations the same way, `0001` upward, and the numbers collided exactly.
A push would have skipped this system's `0001` to `0007` as already applied, because the remote history held Accounting's rows under those version strings, and every later file would then have failed against schemas that were never created.
Worse, this system's remaining files would have claimed the next version slots, and Accounting's own pending migrations under those numbers would have been treated as applied and skipped for ever.
A dry run caught it. Nothing was pushed.

The files were renamed to Supabase's own convention, a fourteen-digit UTC timestamp, which removes the collision.
It does not remove the underlying problem: the CLI cannot manage a database whose migration history it does not wholly own.
With Accounting's versions recorded remotely and absent locally, `db push` refuses, `--include-all` refuses identically, and the only remedy the CLI offers is `migration repair --status reverted`, which rewrites the other product's history.

So this repository applies its own migrations, with its own ledger, in a table it owns.
`supabase link` stays useful for everything else the CLI does.
`supabase db push`, `supabase db pull` and `supabase migration repair` are never run from here.
See the last two entries in `docs/DECISIONS.md`.

## Applying the migrations

```
export DATABASE_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'

python3 supabase/migrate.py --status     # what is applied, what is pending
python3 supabase/migrate.py --dry-run    # every check, nothing written
python3 supabase/migrate.py              # apply the pending files
```

`DATABASE_URL` is the Supabase session pooler, the same variable the worker uses.
`migrate.py` reads it from the environment and nowhere else: it does not load `.env`, and it has no default connection, so it cannot reach a project by accident.
It needs `psql` on `PATH`, PostgreSQL 13 or newer on the server, and nothing else: standard library only, no uv project and no third-party driver.

What it guarantees:

- Every `*.sql` file in `migrations/` is discovered by extension, in name order.
  Never by prefix glob and never against an expected count.
  A `0*.sql` pattern silently matched nothing the day the files were renamed, and that class of bug has appeared twice in this build, so a file that does not parse as `<14-digit timestamp>_<snake_case>.sql` is a loud error and an empty directory is a loud error.
- Each pending file is applied in its own transaction, **together with its ledger row**.
  A file that fails rolls back whole and the run stops there, leaving every earlier file applied and recorded.
  Fix it and run again: the run resumes at that file.
- Each applied file is recorded in `macro_quality.schema_migration` with its version, filename, sha256 of its content, when it was applied and how long it took.
- Running it twice applies nothing the second time.
- `supabase_migrations.schema_migrations` is never written, and is never read for a decision.
  A migration that so much as mentions it is refused.

It also refuses, before anything is applied:

- a recorded migration whose file has changed on disk, or has been renamed, or has been deleted
- a pending file whose timestamp sorts before one that is already applied
- a file containing `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`, `CREATE INDEX CONCURRENTLY`, `ALTER SYSTEM`, `VACUUM` or a `psql` meta-command, because any of those would break the one-file-one-transaction guarantee

That last check is the one `20260921001200_mart_judgment_views.sql` depends on.
It drops and recreates nine views, and a reader must never see the window between the drop and the create.
The per-file transaction gives that, and the file is checked rather than assumed to be well behaved.
There is a second, runtime assertion inside the transaction: if the marker set at its start is gone by the end, the file opened or closed a transaction of its own and the run stops before the ledger records a half-applied file.

### The drift risk, stated plainly

A hand-rolled migrator fails when somebody changes the database without recording it, or records something without applying it.
There is no remote authority to reconcile against, because the one Supabase provides is Accounting's and is not ours to use.

So the ledger is the point of the exercise and not an accessory to it.

- **Editing an applied migration is the failure mode.** The content hash exists to make it loud.
  If `migrate.py` refuses with `has changed since it was applied`, the right fix is almost always a new migration that makes the change, not an edit to the old one.
  An applied migration is a statement about what the database already is; editing one makes the ledger a record of something that never happened.
- **Applying SQL by hand in the dashboard is the other failure mode.** It leaves no ledger row, so the next run has no way to know, and the migration that was going to make that same change will fail on the second attempt.
  If something must be done by hand, write it as a migration first and apply it through this script.
- **If the hash refusal is genuinely cosmetic** (a comment, a typo in a comment) and the database is known to match, update `content_hash` in `macro_quality.schema_migration` by hand and say so in the commit message.
  That is a deliberate, recorded act, which is the difference between this and drift.
- The ledger lives in `macro_quality`, which is on the Data API, so the operator can read the migration history from the dashboard without a psql session.

A third database-scoped collision of this kind should reopen the one-shared-project decision.
Roles, extensions and the `supabase_realtime` publication are the candidates.

## What each file does

| File | What it creates |
| --- | --- |
| `20260921000100_schemas.sql` | The four `macro_` schemas and `pgcrypto`. |
| `20260921000200_raw_core_observed.sql` | `macro_raw.batch` and the observed-fact half of `macro_core`: series, observation, catalyst, watchlist_change, holding, tail_risk, event. |
| `20260921000300_core_judgment.sql` | The asserted-judgment half of `macro_core`, the lifecycle columns, and the partial unique indexes that allow at most one active row. |
| `20260921000400_core_grades.sql` | `week_grade` and `setup_grade`, and the teaching-week foreign key onto graded weeks. |
| `20260921000500_core_constraints.sql` | The deferred constraint triggers: three scenarios summing to 100 per set, four conditions per phase call. Plus the lifecycle CHECKs. |
| `20260921000600_mart_tables.sql` | The eight derived `macro_mart` read models. |
| `20260921000700_mart_history_views.sql` | `scenario_set_history` and `phase_call_history`, the two views that are not derived and must never be dropped and rebuilt. |
| `20260921000800_quality_tables.sql` | `ingest_run`, `finding`, `gap`. |
| `20260921000900_rls.sql` | RLS on every table, `select` for `authenticated` on `macro_mart` and `macro_quality` only, nothing for `anon`, everything for `service_role`. |
| `20260921001000_seed.sql` | The single `macro_mart.calibration` row. |
| `20260921001100_mart_column_additions.sql` | Widens the worker-populated `macro_mart` tables and `macro_quality.gap` to the contract's current column lists. Additive only. |
| `20260921001200_mart_judgment_views.sql` | The nine judgment views the three routes read: `phase_call_detail`, `scenario_set_state`, `strategy_state`, `week_grade`, `setup_grade`, `scenario_rule`, `observation_history`, `setup_history`, `strategy_history`. |
| `20260921001300_condition_void_detail.sql` | `condition_state.void_at` and `void_reason`, so a void clause can say which catalyst moved and when. |
| `20260921001400_reachability_gaps.sql` | `macro_mart.scenario_history`, and the rest of what `/history` could not reach. |
| `20260921001500_event_natural_key.sql` | A natural key on `macro_core.event`, so a replayed run stops appending the log again. |
| `20260921001600_observation_history_note.sql` | Adds `note` to `observation_history`, so a past Snapshot row can show the read written at the time. |

The ledger table `macro_quality.schema_migration` is not in this list.
`migrate.py` creates it itself, before it applies anything, because a migrator cannot record its own bootstrap in a table that does not exist yet.
`20260921000900_rls.sql` then picks it up like any other `macro_quality` table and gives it the same RLS and the same select policy, so the operator can read the migration history from the dashboard.

`macro_core.series` is not seeded by SQL.
`config/series.yaml` is its one source and the worker applies it, because a unit or a release rule written in two places is a unit that will eventually disagree with itself.

## The one manual dashboard step

The Data API only exposes schemas that are explicitly listed, and that list is per project, not per repository.
In the dashboard, go to Settings, then API, then Exposed schemas, and **add** `macro_mart` and `macro_quality` to whatever is already there.
Accounting's `mart` and `quality` are already on that list and must stay on it.
Leave `macro_raw` and `macro_core` off: they are reachable only through the service-role key, and through the two history views in `macro_mart`.

Auth needs no new step.
This system uses the same single operator account as Accounting, with public signups already disabled.

## The `macro_desk` login

`20260924000200_desk_role.sql` creates the role `macro_desk` with NOLOGIN, because a committed file never carries a password.
After the migrations are applied, once, on the operator's machine:

```
python3 supabase/desk_role.py            # sets LOGIN and a generated password, writes MACRO_DESK_DATABASE_URL into .env
python3 supabase/desk_role.py --rotate   # a new password; the old one stops working
python3 supabase/desk_role.py --dry-run  # checks the role exists and says what it would do
```

It sends the server a SCRAM-SHA-256 verifier computed locally, never the password, and passes credentials to `psql` through the environment, never on the command line.
The URL it writes keeps `DATABASE_URL`'s pooler host, port and options with the user `macro_desk.<project-ref>`, and it connects with it before reporting success.
Roles are cluster-wide rather than per database, which is why the name carries the `macro_` prefix.

## Two conventions the schema enforces that are easy to trip over

**An activation is two statements, in this order.**
The "at most one active row" rules are partial unique indexes, and a partial unique index cannot be deferred.
Stand the outgoing row down to `superseded` first, then stand the incoming row up to `active`, both in one transaction.
Activating first leaves two active rows for the length of one statement and is rejected.

**Most of `macro_mart` is views over `macro_core`, and every one of them grants itself.**
`20260921000900_rls.sql` runs before them, so its "grant select on all tables in schema macro_mart" cannot reach any view created after it.
Each of those files carries its own `revoke` and `grant` statements.
A view added to `macro_mart` later must do the same, or the web app will not be able to see it, and `tests/smoke.sql` will say so.

**A scenario set and a phase call are written whole, in one transaction.**
The two cardinality rules are `DEFERRABLE INITIALLY DEFERRED` constraint triggers, so they fire at `COMMIT`.
A set committed with two scenarios, or a phase call committed with three conditions, is rejected at that point.
Do not call `SET CONSTRAINTS ALL IMMEDIATE` while a set is half-written.

## Verifying

`tests/` is not part of the migration set.
Against a throwaway local Postgres:

```
createdb macro_shim_test
psql -d macro_shim_test -f supabase/tests/local_shim.sql
for f in supabase/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -d macro_shim_test -f "$f"; done
psql -v ON_ERROR_STOP=1 -d macro_shim_test -f supabase/tests/smoke.sql
```

There is also a self-contained harness that creates its own throwaway cluster, exercises `migrate.py` and tears the cluster down:

```
python3 supabase/tests/migrate_test.py
```

It covers a clean database taking every file, a second run applying nothing, a failing file rolling back whole and leaving the ledger consistent, an edited applied file being refused by hash, and a database that already holds a simulated Accounting plus rows in `supabase_migrations.schema_migrations` coming through with that table not merely unchanged but never written, asserted on PostgreSQL's own insert, update and delete counters with a positive control on our ledger so a statistics failure cannot make it pass by accident.
It prints `MIGRATE OK`.

`local_shim.sql` stands in for the Supabase-managed `anon`, `authenticated` and `service_role` roles and `auth.uid()`.
`smoke.sql` checks the cardinality triggers, the lifecycle rules, the active-row uniqueness, the grants, the columns `20260921001100` adds and the CHECKs that come with them, and every view in `macro_mart`.
For the views it writes one full week of judgment, reads each view back as `authenticated`, and asserts that the row its route needs came out of it, including the two `jsonb` aggregates on `strategy_state` and the `sector_focus_id` resolution on `setup_history`.
It also checks that `anon` is refused on every view, and, where the harness has planted one, that a simulated Accounting `raw`, `core`, `mart` and `quality` keeps its own grants, policies and `core` to `mart` view.
It counts the views from the catalogue rather than against a written-down number, for the same reason `migrate.py` does not glob on a prefix.
It prints `SMOKE OK` and leaves no rows behind.

Against the live project, the useful check is the same one Accounting documents.
`anon` should fail to select from `macro_mart.snapshot_row` and everything else.
`authenticated`, signed in as the operator, should read `macro_mart.*` and `macro_quality.*` and fail on `macro_raw.*` and `macro_core.*`.
The worker uses the service-role key exclusively and never runs inside the browser or the web app.
