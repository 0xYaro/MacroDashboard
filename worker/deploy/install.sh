#!/bin/bash
#
# Install the MacroDashboard worker as a launchd user agent.
#
# Every path is derived from this script's own location, so moving the
# repository does not break the installed agent: reinstall and it follows.
#
# This script does not create, read or write any .env file. The worker reads its
# configuration from the environment, and how the operator gets it there is
# their business.
set -euo pipefail

LABEL="com.rayclaw.macro-worker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORKER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
REPO_DIR="$(cd "${WORKER_DIR}/.." && pwd -P)"
TEMPLATE="${SCRIPT_DIR}/${LABEL}.plist"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
TARGET="${AGENTS_DIR}/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/macro-worker"

die() { printf 'install.sh: %s\n' "$1" >&2; exit 1; }

[[ -f "${TEMPLATE}" ]] || die "the plist template is missing: ${TEMPLATE}"
[[ -f "${WORKER_DIR}/pyproject.toml" ]] || die "no worker project at ${WORKER_DIR}"
[[ -f "${REPO_DIR}/config/series.yaml" ]] \
  || die "config/series.yaml is missing at ${REPO_DIR}/config; it is the one source for macro_core.series and the worker will not start without it"

# uv is resolved to an absolute path: launchd hands an agent almost no PATH, so
# a bare `uv` in ProgramArguments would fail with a posix_spawn error that says
# nothing useful.
UV_BIN="$(command -v uv || true)"
[[ -n "${UV_BIN}" ]] || die "uv is not on PATH. Install it from https://docs.astral.sh/uv/"
UV_BIN="$(cd "$(dirname "${UV_BIN}")" && pwd -P)/$(basename "${UV_BIN}")"

# The two runs are scheduled in the machine's local time zone, because launchd
# has no time-zone key. They are meant to be 09:30 and 21:00 Asia/Singapore.
TZ_NAME="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
printf 'repository : %s\n' "${REPO_DIR}"
printf 'worker     : %s\n' "${WORKER_DIR}"
printf 'uv         : %s\n' "${UV_BIN}"
printf 'logs       : %s\n' "${LOG_DIR}"
printf 'time zone  : %s\n' "${TZ_NAME:-unknown}"
if [[ "${TZ_NAME}" != "Asia/Singapore" ]]; then
  printf '\n  WARNING: the two runs are scheduled at 09:30 and 21:00 local time and\n'
  printf '  this machine is not on Asia/Singapore. The 09:30 run assumes the US\n'
  printf '  close has settled and the two OpenClaw crons have already written.\n\n'
fi

mkdir -p "${AGENTS_DIR}" "${LOG_DIR}"

# Resolve the environment before the first run, so a dependency failure shows up
# here rather than as a scheduled run that silently does nothing at 09:30.
printf '\nresolving the worker environment\n'
( cd "${WORKER_DIR}" && "${UV_BIN}" sync --frozen ) \
  || die "uv sync failed; fix that before installing the agent"

# `|` as the sed delimiter, because every replacement is a path.
sed \
  -e "s|__UV_BIN__|${UV_BIN}|g" \
  -e "s|__WORKER_DIR__|${WORKER_DIR}|g" \
  -e "s|__LOG_DIR__|${LOG_DIR}|g" \
  -e "s|__HOME__|${HOME}|g" \
  -e "s|__PATH__|$(dirname "${UV_BIN}"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin|g" \
  "${TEMPLATE}" > "${TARGET}"

plutil -lint "${TARGET}" >/dev/null || die "the rendered plist is not valid: ${TARGET}"
printf 'wrote %s\n' "${TARGET}"

# The run queue's ticker. It needs `claude` on PATH as well as uv, because it
# starts the headless sessions; the path is resolved here for the same reason
# uv's is.
RUNNER_LABEL="com.rayclaw.macro-runner"
RUNNER_TEMPLATE="${SCRIPT_DIR}/${RUNNER_LABEL}.plist"
RUNNER_TARGET="${AGENTS_DIR}/${RUNNER_LABEL}.plist"
CLAUDE_BIN="$(command -v claude || true)"
[[ -n "${CLAUDE_BIN}" ]] || die "claude is not on PATH; the run queue cannot start a session without it"
( cd "${REPO_DIR}/agent" && "${UV_BIN}" sync --frozen ) \
  || die "uv sync failed for agent/; fix that before installing the runner"
sed \
  -e "s|__UV_BIN__|${UV_BIN}|g" \
  -e "s|__REPO_DIR__|${REPO_DIR}|g" \
  -e "s|__LOG_DIR__|${LOG_DIR}|g" \
  -e "s|__HOME__|${HOME}|g" \
  -e "s|__PATH__|$(dirname "${UV_BIN}"):$(dirname "${CLAUDE_BIN}"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin|g" \
  "${RUNNER_TEMPLATE}" > "${RUNNER_TARGET}"
plutil -lint "${RUNNER_TARGET}" >/dev/null || die "the rendered plist is not valid: ${RUNNER_TARGET}"
printf 'wrote %s\n' "${RUNNER_TARGET}"

# `bootout` first so a reinstall replaces the loaded agent rather than being
# refused for already being loaded. It fails when nothing is loaded, which is
# fine on a first install.
DOMAIN="gui/$(id -u)"
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "${TARGET}"
launchctl enable "${DOMAIN}/${LABEL}"
launchctl bootout "${DOMAIN}/${RUNNER_LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "${RUNNER_TARGET}"
launchctl enable "${DOMAIN}/${RUNNER_LABEL}"

printf '\nloaded. the agent waits for 09:30 and 21:00; it does not run at load.\n'
printf 'useful commands:\n'
printf '  launchctl print %s/%s | head -40\n' "${DOMAIN}" "${LABEL}"
printf '  launchctl kickstart -k %s/%s   # run it now\n' "${DOMAIN}" "${LABEL}"
printf '  tail -f %s/worker.err.log\n' "${LOG_DIR}"
printf '  tail -f %s/runner.err.log   # the run queue, every two minutes\n' "${LOG_DIR}"
printf '  %s/uninstall.sh\n' "${SCRIPT_DIR}"
