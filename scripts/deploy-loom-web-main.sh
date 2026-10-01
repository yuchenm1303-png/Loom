#!/usr/bin/env bash
set -euo pipefail

SRC="${LOOM_WEB_SOURCE_DIR:-/opt/loom-web-main-src}"
DEPLOY="${LOOM_WEB_DEPLOY_DIR:-/opt/loom-web-deploy}"
MARKER="$DEPLOY/deployed-main"
LOCK_FILE="${LOOM_WEB_LOCK_FILE:-/var/lock/loom-web-autosync.lock}"
LOG_PREFIX='[loom-web-autosync]'

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$SRC"
git fetch -q origin main
remote="$(git rev-parse origin/main)"
deployed="$(cat "$MARKER" 2>/dev/null || true)"
if [[ "$remote" == "$deployed" ]]; then
  exit 0
fi

# Deployment checkout only: GitHub main is the sole source for the shared
# Desktop/Web renderer. No server-side UI overlay is permitted.
git reset --hard -q origin/main
candidate="loom-web:autosync-${remote:0:12}"
echo "$LOG_PREFIX building main=${remote:0:12}"
docker build -q \
  --build-arg "LOOM_BUILD_SHA=$remote" \
  -f services/loom_web_gateway/Dockerfile \
  -t "$candidate" . >/dev/null

old_id="$(docker image inspect loom-web:independent --format '{{.Id}}' 2>/dev/null || true)"
if [[ -n "$old_id" ]]; then
  docker tag "$old_id" loom-web:autosync-rollback
fi
docker tag "$candidate" loom-web:independent

rollback() {
  echo "$LOG_PREFIX candidate failed verification; rolling back" >&2
  if docker image inspect loom-web:autosync-rollback >/dev/null 2>&1; then
    docker tag loom-web:autosync-rollback loom-web:independent
    (cd "$DEPLOY" && docker compose -f compose.prod.yml up -d --force-recreate >/dev/null)
  fi
  exit 13
}

cd "$DEPLOY"
docker compose -f compose.prod.yml up -d --force-recreate >/dev/null
healthy=0
for _ in $(seq 1 30); do
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' loom-web 2>/dev/null || true)"
  if [[ "$state" == healthy ]]; then healthy=1; break; fi
  if [[ "$state" == unhealthy || "$state" == exited ]]; then break; fi
  sleep 2
done
[[ "$healthy" == 1 ]] || rollback

actual="$(docker exec loom-web python -c 'import os; print(os.environ.get("LOOM_BUILD_SHA", ""))' 2>/dev/null || true)"
[[ "$actual" == "$remote" ]] || rollback

# The marker is written only after the running container proves which Git commit
# it contains. This prevents a successful-looking deploy marker from hiding a
# stale bundle.
tmp="$MARKER.tmp.$$"
printf '%s\n' "$remote" >"$tmp"
mv "$tmp" "$MARKER"

# Keep the stable production tag plus one rollback image; discard the per-build
# alias so repeated UI deploys do not accumulate tags forever.
docker image rm "$candidate" >/dev/null 2>&1 || true
echo "$LOG_PREFIX deployed main=${remote:0:12}"
