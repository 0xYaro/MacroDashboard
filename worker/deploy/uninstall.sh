#!/bin/bash
#
# Remove the MacroDashboard worker's launchd user agent.
#
# Logs in ~/Library/Logs/macro-worker are kept. They are the record of what the
# worker did, and an uninstall is not a reason to destroy it. Delete them by
# hand if you want them gone.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LOG_DIR="${HOME}/Library/Logs/macro-worker"
DOMAIN="gui/$(id -u)"

for LABEL in com.rayclaw.macro-worker com.rayclaw.macro-runner; do
  TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"
  if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
    launchctl bootout "${DOMAIN}/${LABEL}"
    printf 'unloaded %s\n' "${LABEL}"
  else
    printf '%s was not loaded\n' "${LABEL}"
  fi
  if [[ -f "${TARGET}" ]]; then
    rm -f "${TARGET}"
    printf 'removed %s\n' "${TARGET}"
  else
    printf 'no plist at %s\n' "${TARGET}"
  fi
done

printf '\nlogs kept at %s\n' "${LOG_DIR}"
printf 'reinstall with %s/install.sh\n' "${SCRIPT_DIR}"
