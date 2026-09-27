#!/usr/bin/env bash
# Install or update the OpenClaw macro-desk agent from this repository.
#
#   openclaw/install.sh [all|files|config|jobs]      (default: all)
#
# files   copy the plugin into ~/.openclaw/extensions/macro-desk (then npm install,
#         build, validate) and create the agent's empty, read-only workspace
# config  enable the plugin with its config, create the agent if missing, and set
#         the agent's model, workspace, tools, tool-result limit and locked Codex home
# jobs    create the four cron jobs, or update an existing job's prompt, schedule,
#         model, context mode and failure alert in place
#
# Idempotent: run it again after any change under openclaw/. It prints every
# openclaw command before running it, with the Telegram group id shown as <group>.
#
# Environment:
#   DESK_MODE=shadow|live   plugin mode; unset keeps the current mode (shadow when new)
#   DESK_THREAD_ID=<n>      Telegram topic for the four jobs' reports; unset leaves an
#                           existing job's delivery alone and creates new jobs with none.
#                           Failure alerts always go where the old job for the same feed
#                           sends its own (the operator's alert chat), never to a topic.
#   DESK_THREAD_CATALYSTS, DESK_THREAD_HOLDINGS, DESK_THREAD_TAIL_RISKS
#                           a topic per feed, overriding DESK_THREAD_ID for that feed
#                           (the cutover uses 471, 500 and 686)
#   DESK_TELEGRAM_TO=<id>   the group; unset reads it from daily-market-catalyst-monitor
#   UV_PATH, OPENCLAW_BIN   overrides for the uv and openclaw binaries

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/openclaw"
OC="${OPENCLAW_BIN:-/opt/homebrew/bin/openclaw}"
OC_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
AGENT_ID="macro-desk"
PLUGIN_ID="macro-desk"
# Outside ~/.openclaw/workspace, which is the main agent's git repository: Codex
# would otherwise load that repository's AGENTS.md into every desk run.
WORKSPACE="$OC_HOME/desk-workspaces/$AGENT_ID"
OLD_WORKSPACE="$OC_HOME/workspace/$AGENT_ID"
RULES="$SRC/desk-rules.md"
CODEX_HOME_DIR="$OC_HOME/agents/$AGENT_ID/agent/codex-home"
# The Codex dynamic-tool result cap (agents.list[].contextLimits.toolResultMaxChars);
# the default of 16,000 characters cut the catalyst calendar mid-JSON.
TOOL_RESULT_MAX_CHARS=64000
EXT="$OC_HOME/extensions/$PLUGIN_ID"
UV="${UV_PATH:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
MODEL="openai/gpt-5.6-sol"
FALLBACKS="openai/gpt-5.6-terra,openai/gpt-5.5"
JSON5="/opt/homebrew/lib/node_modules/openclaw/node_modules/json5"
GROUP=""
ALERT_TO=""

redact() {
  local text="$*"
  if [[ -n "$GROUP" ]]; then text="${text//$GROUP/<group>}"; fi
  if [[ -n "$ALERT_TO" ]]; then text="${text//$ALERT_TO/<alert chat>}"; fi
  printf '%s' "$text"
}

# Print, then run. A --message value is shown as the prompt file it came from.
run() {
  local shown=() skip=0 arg
  for arg in "$@"; do
    if (( skip )); then shown+=("<${MESSAGE_FILE#"$REPO"/} + openclaw/desk-rules.md>"); skip=0; continue; fi
    [[ "$arg" == "--message" ]] && skip=1
    shown+=("$arg")
  done
  printf '+ %s\n' "$(redact "${shown[*]}")" >&2
  "$@"
}

config_json() {
  # Read-only: print a JS expression evaluated against openclaw.json.
  node -e '
    const JSON5 = require(process.argv[1]);
    const c = JSON5.parse(require("fs").readFileSync(process.argv[2], "utf8"));
    const out = (new Function("c", "return (" + process.argv[3] + ")"))(c);
    process.stdout.write(out === undefined ? "" : typeof out === "string" ? out : JSON.stringify(out));
  ' "$JSON5" "$OC_HOME/openclaw.json" "$1"
}

do_files() {
  echo "== files"
  mkdir -p "$EXT"
  rsync -a --delete --exclude node_modules --exclude dist --exclude '*.tgz' --exclude package-lock.json \
    "$SRC/plugin/macro-desk/" "$EXT/"
  # The lock file pins file: paths relative to the repo, so the copy resolves its own.
  (cd "$EXT" && npm install --no-audit --no-fund --silent && npm run --silent build \
     && "$OC" plugins validate --entry ./dist/index.js)
  workspace
  echo "plugin in $EXT, empty read-only workspace $WORKSPACE"
}

# The agent's workspace holds nothing: the rules travel in each job's message, so
# there is no instruction file a run could rewrite for the next one. It is mode 555
# as well, so nothing can be created in it.
workspace() {
  if [[ -d "$WORKSPACE" ]]; then chmod 0755 "$WORKSPACE"; else mkdir -p "$WORKSPACE"; fi
  rm -f "$WORKSPACE/AGENTS.md"
  if [[ -n "$(ls -A "$WORKSPACE")" ]]; then
    echo "$WORKSPACE is not empty; the desk workspace must hold nothing:" >&2
    ls -A "$WORKSPACE" >&2
    exit 2
  fi
  chmod 0555 "$WORKSPACE"
  # The first install put the workspace inside the main agent's git repository.
  if [[ -d "$OLD_WORKSPACE" ]]; then
    rm -f "$OLD_WORKSPACE/AGENTS.md"
    rmdir "$OLD_WORKSPACE" 2>/dev/null || echo "left $OLD_WORKSPACE in place: it holds files this script did not write" >&2
  fi
}

do_config() {
  echo "== config"
  local mode allow index tools repo_path
  mode="${DESK_MODE:-$(config_json 'c.plugins?.entries?.["macro-desk"]?.config?.mode ?? "shadow"')}"
  if [[ "$mode" != "shadow" && "$mode" != "live" ]]; then
    echo "DESK_MODE must be shadow or live, not $mode" >&2
    exit 2
  fi
  allow="$(config_json '(c.plugins?.allow ?? []).includes("macro-desk") ? "" : JSON.stringify([...(c.plugins?.allow ?? []), "macro-desk"])')"
  if [[ -n "$allow" ]]; then
    run "$OC" config set plugins.allow "$allow" --strict-json
  fi
  # macOS paths are case-insensitive, and a shell started in a differently cased
  # folder would otherwise rewrite the same repoPath in another case.
  repo_path="$(config_json 'c.plugins?.entries?.["macro-desk"]?.config?.repoPath ?? ""')"
  if [[ "$(tr '[:upper:]' '[:lower:]' <<<"$repo_path")" != "$(tr '[:upper:]' '[:lower:]' <<<"$REPO")" ]]; then repo_path="$REPO"; fi
  run "$OC" config set "plugins.entries.$PLUGIN_ID" \
    "{\"enabled\":true,\"config\":{\"repoPath\":\"$repo_path\",\"uvPath\":\"$UV\",\"mode\":\"$mode\"}}" --strict-json

  index="$(config_json 'c.agents.list.findIndex((a) => a.id === "macro-desk")')"
  if [[ "$index" == "-1" ]]; then
    run "$OC" agents add "$AGENT_ID" --workspace "$WORKSPACE" --model "$MODEL" --non-interactive
    index="$(config_json 'c.agents.list.findIndex((a) => a.id === "macro-desk")')"
  fi
  tools='{
    "profile": "minimal",
    "alsoAllow": ["read", "web_search", "web_fetch", "desk_context", "catalysts_list", "catalysts_submit",
                  "tail_risks_list", "tail_risks_submit", "positions_read", "holdings_watch_submit"],
    "deny": ["write", "edit", "apply_patch", "process", "browser", "sessions_spawn", "subagents"],
    "exec": {"security": "allowlist", "ask": "on-miss", "strictInlineEval": true},
    "fs": {"workspaceOnly": true}
  }'
  run "$OC" config set "agents.list[$index].name" "$AGENT_ID"
  run "$OC" config set "agents.list[$index].identity" '{"name":"Macro Desk"}' --strict-json
  run "$OC" config set "agents.list[$index].model" "$MODEL"
  run "$OC" config set "agents.list[$index].workspace" "$WORKSPACE"
  run "$OC" config set "agents.list[$index].skills" '[]' --strict-json
  run "$OC" config set "agents.list[$index].tools" "$(tr -d '\n' <<<"$tools")" --strict-json
  run "$OC" config set "agents.list[$index].contextLimits" "{\"toolResultMaxChars\":$TOOL_RESULT_MAX_CHARS}" --strict-json
  run "$OC" config validate
  codex_features
}

# The macro desk works only through its desk tools and web search, so this
# agent's own Codex home (CODEX_HOME of its app-server) switches off everything
# else Codex would offer it or inject into its prompt. Each line was checked with
# a probe run that listed the tools and the injected instructions (README):
#   - the shell and unified exec: Codex runs them in a workspace-write sandbox
#     with no network, but that sandbox can still read any file this user can;
#   - apply_patch, the only remaining way to write a file: OpenClaw's tool deny
#     does not reach Codex's own tool, and with gpt-5.6 the model catalog, not a
#     feature flag, offers it (apply_patch_tool_type), so this home uses its own
#     copy of the catalog with that field cleared;
#   - sub-agents: the catalog's multi_agent_version turns on the collaboration
#     tools and a "you are /root" prompt whatever features.multi_agent says, so
#     the same copy drops that field;
#   - view_image, which reads any image file by path: the catalog copy also lists
#     text as the only input, since Codex offers view_image to image-input models;
#   - goals, apps, plugins, image generation, browser and computer use;
#   - the three global MCP servers (serena, playwright, context7), each a way to
#     read files or reach the network outside the sandbox. A disabled entry needs
#     a command, or Codex rejects the table and hosted web search is lost with it;
#   - injected text: project docs (AGENTS.md files between the workspace and a
#     git root) and the skills list.
# The file is regenerated whole on every install; only [projects.*] trust entries
# that Codex or OpenClaw added are carried over. Every other agent keeps its setup.
codex_features() {
  local file="$CODEX_HOME_DIR/config.toml"
  local catalog="$CODEX_HOME_DIR/macro-desk-model-catalog.json"
  mkdir -p "$CODEX_HOME_DIR"
  touch "$file"
  python3 - "$file" "$catalog" "$CODEX_HOME_DIR/models_cache.json" "$WORKSPACE" <<'PY'
import json, os, sys, tomllib

path, catalog_path, cache_path, workspace = sys.argv[1:5]

def toml_str(value):
    return json.dumps(value)

# The model catalog: Codex's cached copy with the three fields that add tools cleared.
catalog_line = ""
if os.path.exists(cache_path):
    cache = json.load(open(cache_path))
    models = cache.get("models", [])
    for model in models:
        model.pop("multi_agent_version", None)
        model["apply_patch_tool_type"] = None
        # view_image is offered to a model that takes image input; the desk reads text only.
        model["input_modalities"] = ["text"]
    tmp = catalog_path + ".tmp"
    with open(tmp, "w") as out:
        json.dump({"models": models}, out)
    os.replace(tmp, catalog_path)
    catalog_line = f"model_catalog_json = {toml_str(catalog_path)}\n"
    print(f"model catalog with apply_patch, view_image and sub-agents cleared: {len(models)} models")
else:
    print(f"no {cache_path} yet: run the agent once, then install again, or apply_patch and sub-agents stay on",
          file=sys.stderr)

try:
    existing = tomllib.loads(open(path).read())
except tomllib.TOMLDecodeError:
    existing = {}
projects = {
    key: value
    for key, value in (existing.get("projects") or {}).items()
    if isinstance(value, dict) and key != os.path.dirname(os.path.dirname(workspace)) + "/workspace"
}

text = f"""# macro-desk: locked tool surface (written by MacroDashboard openclaw/install.sh;
# edits here are overwritten on the next install)
{catalog_line}project_doc_max_bytes = 0
project_root_markers = []

[features]
shell_tool = false
unified_exec = false
apps = false
plugins = false
tool_suggest = false
multi_agent = false
multi_agent_v2 = false
goals = false
memories = false
image_generation = false
browser_use = false
in_app_browser = false
computer_use = false

[skills]
include_instructions = false
"""
for server in ("serena", "playwright", "context7"):
    text += f"""
[mcp_servers.{server}]
command = "/usr/bin/false"
enabled = false
"""
for key, value in sorted(projects.items()):
    text += f"\n[projects.{toml_str(key)}]\n"
    for field, one in value.items():
        text += f"{field} = {toml_str(one)}\n"
tomllib.loads(text)
tmp = path + ".tmp"
with open(tmp, "w") as out:
    out.write(text)
os.replace(tmp, path)
PY
  echo "locked Codex tool surface in $file"
}

job_id() {
  "$OC" cron list --all --json 2>/dev/null | node -e '
    let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => {
      const job = JSON.parse(s).jobs.find((j) => j.name === process.argv[1]);
      process.stdout.write(job ? job.id : "");
    });' "$1"
}

# Read-only: one old job's failure-alert settings as "channel|to|after|cooldownMs|accountId".
old_failure_alert() {
  "$OC" cron list --all --json 2>/dev/null | node -e '
    let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => {
      const job = JSON.parse(s).jobs.find((j) => j.name === process.argv[1]);
      const a = job?.failureAlert;
      if (!a || !a.channel || !a.to) return;
      process.stdout.write([a.channel, a.to, a.after ?? 1, a.cooldownMs ?? 21600000, a.accountId ?? ""].join("|"));
    });' "$1"
}

do_jobs() {
  echo "== jobs"
  GROUP="${DESK_TELEGRAM_TO:-}"
  if [[ -z "$GROUP" ]]; then
    GROUP="$("$OC" cron list --all --json 2>/dev/null | node -e '
      let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => {
        const job = JSON.parse(s).jobs.find((j) => j.name === "daily-market-catalyst-monitor");
        process.stdout.write(job?.delivery?.to ?? "");
      });')"
  fi
  local name cron prompt thinking timeout feed old id delivery message thread alert
  local a_channel a_to a_after a_cooldown a_account alert_args
  # A job-level tool list (--tools) would switch off Codex's native tool surface,
  # and with it hosted web search, so the jobs carry none: the agent's own tool
  # policy is the allowlist. Each message is the job's prompt followed by the
  # desk rules; the workspace holds no instruction file. --light-context keeps
  # OpenClaw's workspace bootstrap out and sets Codex's project_doc_max_bytes to 0
  # for the run, so no AGENTS.md from any folder is injected.
  # name | schedule | prompt | thinking | timeout seconds | feed | old job (failure-alert target)
  while IFS='|' read -r name cron prompt thinking timeout feed old; do
    [[ -z "$name" ]] && continue
    MESSAGE_FILE="$SRC/prompts/$prompt"
    message="$(cat "$MESSAGE_FILE"; printf '\n\n---\n\n'; cat "$RULES")"
    case "$feed" in
      catalysts) thread="${DESK_THREAD_CATALYSTS:-${DESK_THREAD_ID:-}}" ;;
      holdings) thread="${DESK_THREAD_HOLDINGS:-${DESK_THREAD_ID:-}}" ;;
      *) thread="${DESK_THREAD_TAIL_RISKS:-${DESK_THREAD_ID:-}}" ;;
    esac
    delivery=()
    if [[ -n "$thread" ]]; then
      if [[ -z "$GROUP" ]]; then echo "no Telegram group found; set DESK_TELEGRAM_TO" >&2; exit 2; fi
      delivery=(--announce --channel telegram --to "$GROUP" --thread-id "$thread")
    fi
    id="$(job_id "$name")"
    if [[ -z "$id" ]]; then
      if (( ${#delivery[@]} == 0 )); then delivery=(--no-deliver); fi
      run "$OC" cron add --name "$name" --agent "$AGENT_ID" --session isolated \
        --cron "$cron" --tz Asia/Singapore --exact --wake now \
        --model "$MODEL" --fallbacks "$FALLBACKS" --thinking "$thinking" \
        --timeout-seconds "$timeout" --light-context \
        --description "Macro desk trial: $prompt through the desk tools (repo openclaw/prompts/$prompt)." \
        ${delivery[@]+"${delivery[@]}"} --message "$message" >/dev/null
      id="$(job_id "$name")"
    else
      run "$OC" cron edit "$id" --agent "$AGENT_ID" --cron "$cron" --tz Asia/Singapore --exact \
        --model "$MODEL" --fallbacks "$FALLBACKS" --thinking "$thinking" \
        --timeout-seconds "$timeout" --clear-tools --light-context \
        ${delivery[@]+"${delivery[@]}"} --message "$message" >/dev/null
    fi
    # Failure alerts go where the old job for the same feed sends its own: the
    # operator's alert chat, not a public topic. Normal delivery is untouched.
    alert="$(old_failure_alert "$old")"
    if [[ -n "$alert" ]]; then
      IFS='|' read -r a_channel a_to a_after a_cooldown a_account <<<"$alert"
      ALERT_TO="$a_to"
      alert_args=(--failure-alert --failure-alert-after "$a_after" --failure-alert-channel "$a_channel"
        --failure-alert-to "$a_to" --failure-alert-cooldown "$(( a_cooldown / 60000 ))m" --failure-alert-exclude-skipped)
      if [[ -n "$a_account" ]]; then alert_args+=(--failure-alert-account-id "$a_account"); fi
      run "$OC" cron edit "$id" "${alert_args[@]}" >/dev/null
    else
      echo "$old has no failure alert to copy; $name keeps its current alert settings" >&2
    fi
    echo "$name $id ($cron Asia/Singapore)"
  done <<'JOBS'
desk-catalysts|40 8 * * *|catalysts.md|high|1200|catalysts|daily-market-catalyst-monitor
desk-holdings|5 9 * * *|holdings.md|high|1800|holdings|daily-holdings-watch
desk-tail-risks-daily|20 9 * * *|tail-risks-daily.md|medium|900|tail_risks|weekly-market-blackswan-events
desk-tail-risks-weekly|10 6 * * 1|tail-risks-weekly.md|high|1800|tail_risks|weekly-market-blackswan-events
JOBS
}

case "${1:-all}" in
  files) do_files ;;
  config) do_config ;;
  jobs) do_jobs ;;
  all) do_files; do_config; do_jobs ;;
  *) echo "usage: $0 [all|files|config|jobs]" >&2; exit 2 ;;
esac
