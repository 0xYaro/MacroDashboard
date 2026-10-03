# Rolling back the macro desk

The OpenClaw state before the macro desk was installed is saved in `~/.openclaw/backups/macro-desk-20260924-020242/`:

| File | What it is |
| --- | --- |
| `openclaw.json` | The gateway config as it was (SHA-256 `be83beeba97604dbc005fa8df70fc4778a0e8c475a3bad1fdb05c8c865db25ae`). |
| `cron_jobs.sql` | A SQL dump of the `cron_jobs` table: the nine jobs that existed. |
| `openclaw.sqlite` | A full online backup of `~/.openclaw/state/openclaw.sqlite`. |

The folder is mode 700 and its files 600: the config holds the gateway's secrets.

A second backup was taken before the review fixes of 25 September (the workspace move, the locked Codex home, light context, failure alerts, the tool-result limit): `~/.openclaw/backups/macro-desk-20260925-005559/`.
It holds `openclaw.json` (SHA-256 `4e31c489b3628ab2dd7241670f1c6e67b176668dc973c8c4a5f212e19c9c8b77`), `cron_jobs.sql` (the thirteen jobs), `openclaw.sqlite`, and the agent's `codex-home-config.toml` and `workspace-AGENTS.md` as they were.
To return to exactly that state, use it in place of the first folder in "Last resort" below, and copy `codex-home-config.toml` back to `~/.openclaw/agents/macro-desk/agent/codex-home/config.toml`.

## Pause the trial (keep everything installed)

The quickest undo, and the one to reach for first.
It stops the four desk jobs and touches nothing else.

```bash
for name in desk-catalysts desk-holdings desk-tail-risks-daily desk-tail-risks-weekly; do
  id=$(openclaw cron list --all --json | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s).jobs.find(j=>j.name===process.argv[1]);process.stdout.write(j?j.id:"")})' "$name")
  [ -n "$id" ] && openclaw cron disable "$id"
done
```

## After the cutover: go back to the old jobs

The cutover (see `README.md`) flips the plugin to `live`, points the worker at the desk and disables the three old jobs.
The worker's 09:30 SGT run reads the feed sources from `worker/.env` (not the repository-root `.env`), and pointing it back at the vault is safe only once the vault notes are fresh again, so reverse it in this order:

1. Turn the old jobs back on first:

   ```bash
   openclaw cron enable c9baf20d-0f0f-42d7-9cb2-1ecbe1e85376   # daily-market-catalyst-monitor, 08:45 SGT, thread 471
   openclaw cron enable 8f11f550-c4b8-483e-b4c0-5add3366a15b   # daily-holdings-watch, 09:15 SGT, thread 500
   openclaw cron enable f9b7c501-2cea-45d7-9dc7-5be4f265c5f7   # weekly-market-blackswan-events, Monday 06:00 SGT, thread 686
   ```

2. Stop the desk writing: `DESK_MODE=shadow openclaw/install.sh config`, restart the gateway with the checks in `README.md`, then disable the four desk jobs as in "Pause the trial".
3. Wait for fresh notes: the catalyst note after the 08:45 run and the holdings note after the 09:15 run (and, on a Monday, the tail-risk note after 06:00; on other days the tail-risk note is the last weekly one).
   Check each job's last run with `openclaw cron runs --id <id>` before going on.
4. Before 09:30 SGT the same morning, point the worker back at the notes: in `worker/.env`, set (or delete, since `vault` is the default) `MACRO_FEED_SOURCE_CATALYSTS`, `MACRO_FEED_SOURCE_TAIL_RISKS` and `MACRO_FEED_SOURCE_HOLDINGS` to `vault`.
   If 09:30 has passed, change `worker/.env` any time before the next day's 09:30 run; the worker keeps serving the desk's rows until then.

The rows the desk wrote while live stay in the tables with `feed_source = 'openclaw:macro-desk'`.
The next vault run updates the ones its notes list and retires the rest exactly as before the trial: **a catalyst only the desk found (one the old monitor's note does not list) is retired by the vault parser on that first 09:30 run**.
If one of those matters, add it to the old monitor's note before 09:30, or re-add it by hand afterwards.

## Remove the macro desk completely

```bash
# 1. The four jobs.
for name in desk-catalysts desk-holdings desk-tail-risks-daily desk-tail-risks-weekly; do
  id=$(openclaw cron list --all --json | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s).jobs.find(j=>j.name===process.argv[1]);process.stdout.write(j?j.id:"")})' "$name")
  [ -n "$id" ] && openclaw cron rm "$id"
done

# 2. The agent (its entry in agents.list, its workspace and its state).
openclaw agents delete macro-desk

# 3. The plugin: its config entry, its allowlist entry, then its files.
openclaw config unset plugins.entries.macro-desk
openclaw config set plugins.allow "$(node -e 'const J=require("/opt/homebrew/lib/node_modules/openclaw/node_modules/json5");const c=J.parse(require("fs").readFileSync(process.env.HOME+"/.openclaw/openclaw.json","utf8"));process.stdout.write(JSON.stringify(c.plugins.allow.filter(p=>p!=="macro-desk")))')" --strict-json
chmod 755 ~/.openclaw/desk-workspaces/macro-desk
rm -rf ~/.openclaw/extensions/macro-desk ~/.openclaw/desk-workspaces/macro-desk ~/.openclaw/agents/macro-desk

# 4. Load the plugin set without it.
openclaw gateway restart
```

Check `openclaw agents delete --help` first: if it asks for confirmation, answer it; if the command does not exist in the installed version, remove the `agents.list` entry whose `id` is `macro-desk` with `openclaw config unset 'agents.list[<index>]'`.

## Last resort: restore the saved state

Only if the config or the cron table is damaged beyond the steps above.
This also discards every other change made to OpenClaw since 24 September 2026, 02:02 SGT, so check `openclaw cron list --all` and the config's `meta.lastTouchedAt` first.

```bash
B=~/.openclaw/backups/macro-desk-20260924-020242
openclaw gateway stop
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.before-restore
cp "$B/openclaw.json" ~/.openclaw/openclaw.json
# The cron table only: drop it and reload the dump inside one transaction.
sqlite3 ~/.openclaw/state/openclaw.sqlite "begin; drop table cron_jobs; $(cat "$B/cron_jobs.sql" | sed '/^BEGIN TRANSACTION;$/d; /^COMMIT;$/d') commit;"
openclaw config validate
openclaw gateway start
openclaw cron list --all
```

`openclaw.sqlite` in the folder is the whole state database, for a restore that needs more than the cron table (stop the gateway, copy it over `~/.openclaw/state/openclaw.sqlite`, remove the `-wal` and `-shm` files beside it, start the gateway).
