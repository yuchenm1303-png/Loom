#!/usr/bin/env bash
# Publish an already validated portal bundle while preserving the running gateway.
set -euo pipefail
bundle=${1:-/tmp/loom-modules-build/dist}
[[ -f "$bundle/index.html" ]] || { echo 'Missing built frontend' >&2; exit 1; }
# Reject stale portal bundles before replacing the running frontend.
grep -rFq 'https://api.github.com/repos/yuchenm1303-png/Loom/releases/latest' "$bundle/assets" || {
  echo 'Portal bundle is missing dynamic release metadata; rebuild from the updated source.' >&2
  exit 1
}
compose=/opt/loom-web-deploy/compose.prod.yml
baseline=$(sudo -n docker inspect loom-web --format '{{.Image}}')
backup="loom-web:before-modules-$(date -u +%Y%m%dT%H%M%SZ)"
sudo -n docker tag "$baseline" "$backup"
stage=$(mktemp -d /tmp/loom-module-image.XXXXXX)
cp -r "$bundle" "$stage/dist"
cat > "$stage/Dockerfile" <<'DOCKER'
ARG BASE_IMAGE=loom-web:independent
FROM ${BASE_IMAGE}
COPY --chown=loomweb:loomweb dist/ /srv/loom-web/static/
DOCKER
sudo -n docker build --build-arg "BASE_IMAGE=$backup" -t loom-web:independent "$stage"
sudo -n docker compose -f "$compose" up -d --no-deps --force-recreate loom-web
for attempt in $(seq 1 20); do
  if [[ $(sudo -n docker inspect loom-web --format '{{.State.Health.Status}}') == healthy ]]; then
    printf 'Portal layout deployed. Rollback image: %s\n' "$backup"
    exit 0
  fi
  sleep 2
done
sudo -n docker tag "$backup" loom-web:independent
sudo -n docker compose -f "$compose" up -d --no-deps --force-recreate loom-web
echo "Health check failed; restored $backup" >&2
exit 1
