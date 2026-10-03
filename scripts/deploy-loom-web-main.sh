#!/usr/bin/env bash
set -euo pipefail

SRC="${LOOM_WEB_SOURCE_DIR:-/opt/loom-web-main-src}"
DEPLOY="${LOOM_WEB_DEPLOY_DIR:-/opt/loom-web-deploy}"
ACCOUNT_DEPLOY="${LOOM_ACCOUNT_DEPLOY_DIR:-/opt/loom-account/services/loom_account/deploy}"
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

# The gateway's relay authentication depends on the account API. Build both
# from this same main checkout and activate the account service first.
account_source="$(git ls-tree HEAD -- services/__init__.py services/loom_account | sha256sum | cut -d ' ' -f1)"
account_running="$(docker inspect loom-account --format '{{index .Config.Labels "io.loom.account.source-tree"}}' 2>/dev/null || true)"
account_changed=0
account_candidate="loom-account:autosync-${remote:0:12}"
account_compose=(docker compose --project-directory "$ACCOUNT_DEPLOY" -f "$ACCOUNT_DEPLOY/docker-compose.yml")
if [[ -f "$ACCOUNT_DEPLOY/docker-compose.proxy.yml" ]]; then
  account_compose+=(-f "$ACCOUNT_DEPLOY/docker-compose.proxy.yml")
fi
if [[ "$account_running" != "$account_source" ]]; then
  [[ -f "$ACCOUNT_DEPLOY/docker-compose.yml" ]] || { echo 'Missing account deployment configuration' >&2; exit 12; }
  docker build -q -f services/loom_account/Dockerfile \
    --label "org.opencontainers.image.revision=$remote" \
    --label "io.loom.account.source-tree=$account_source" -t "$account_candidate" . >/dev/null
  docker tag "$(docker inspect loom-account --format '{{.Image}}')" loom-account:autosync-rollback
fi

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
  if [[ "$account_changed" == 1 ]]; then
    docker tag loom-account:autosync-rollback loom-account:local
    "${account_compose[@]}" up -d --no-deps --force-recreate loom-account >/dev/null
  fi
  exit 13
}

if [[ "$account_running" != "$account_source" ]]; then
  docker tag "$account_candidate" loom-account:local
  account_changed=1
  "${account_compose[@]}" up -d --no-deps --force-recreate loom-account >/dev/null || rollback
  account_healthy=0
  for _ in $(seq 1 30); do
    if docker exec loom-account python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/healthz", timeout=3).read()' >/dev/null 2>&1; then
      account_healthy=1
      break
    fi
    sleep 2
  done
  [[ "$account_healthy" == 1 ]] || rollback
fi

cd "$DEPLOY"
docker compose -f compose.prod.yml up -d --force-recreate >/dev/null || rollback
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
if [[ "$account_changed" == 1 ]]; then docker image rm "$account_candidate" >/dev/null 2>&1 || true; fi
echo "$LOG_PREFIX deployed main=${remote:0:12}"
