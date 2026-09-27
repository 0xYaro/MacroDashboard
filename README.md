# MacroDashboard

A daily macro terminal for one operator.

It is the weekly market journal produced by the Obsidian vault's `/journal-research` skill, turned inside out.
Today a Claude run authors a 40KB markdown file each Monday and a linter parses it back into JSON.
Here, Postgres holds the structured state, the state updates twice a day, and prose is a rendering of it rather than its source.

Read `docs/DECISIONS.md` first, then `docs/CONTRACT.md`.
`DECISIONS.md` is append-only and records why each irreversible choice was made.
`CONTRACT.md` is binding and defines the seam between the database, the worker and the web app.
`docs/FUTURE.md` holds work deliberately deferred, with the reason it was deferred.

## What it is for

The weekly journal is good at judgment and bad at time.
Its scenario table, phase trackers and trade setups are all written as checkable conditions, then left unchecked for seven days.
Three consecutive weeks (W37, W38, W39) a setup was blocked by a gate clause that was wrong, and each time the error was found the following Monday.

So the clock is split in two.
**Evidence moves daily**: prices, credit, breadth, flows, and every condition derived from them.
**Judgment moves weekly**: the phase call, the scenario set and its probabilities, the setups, the strategy.
A daily run can tell you a condition flipped; it cannot decide the call has changed.

## Layout

```
supabase/migrations/*.sql   schema, RLS, seed
config/series.yaml          asserted reference data: every series, its cadence and release rule
worker/                     Python 3.12, uv project, two scheduled runs
agent/                      the Claude runs (daily narrative, Monday author, amendments), the run queue, and `macro-agent desk`
openclaw/                   the OpenClaw macro-desk agent: its tool plugin, rules, job prompts and installer
migration/                  the one-off migrator that loaded the journal history
web/                        Next.js 15, React 19, pnpm, deployed on Vercel
docs/                       DECISIONS, CONTRACT, DESK (the desk tool contract), FUTURE
```

## Relationship to the sibling repositories

`Accounting` is the portfolio book: what is held, at what exposure.
`MacroDashboard` is the regime read: what game is being played.
They share one Supabase project and one login so that merging them later is a routing change rather than a data migration, and they share a design system so the two read as one terminal.
Neither imports code from the other.

`GreenfieldDash` is the upstream market terminal and the origin of the design tokens and the front-end doctrine both of these follow.
There is no runtime dependency on it in either direction.
