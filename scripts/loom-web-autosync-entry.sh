#!/usr/bin/env bash
set -euo pipefail

# Keep the installed timer entry small. Always run the deployment script from
# GitHub main rather than a server-local copy that can silently become stale.
SRC="${LOOM_WEB_SOURCE_DIR:-/opt/loom-web-main-src}"
exec 8>"${LOOM_WEB_ENTRY_LOCK_FILE:-/var/lock/loom-web-autosync-entry.lock}"
flock -n 8 || exit 0
git -C "$SRC" fetch -q origin main
task_script=$(mktemp /tmp/loom-web-deploy-main.XXXXXX)
trap 'rm -f "$task_script"' EXIT
git -C "$SRC" show origin/main:scripts/deploy-loom-web-main.sh >"$task_script"
bash "$task_script"
