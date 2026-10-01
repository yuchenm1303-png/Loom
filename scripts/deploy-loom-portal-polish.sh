#!/usr/bin/env bash
# Deploy a presentation-only layer on the currently running Loom portal image.
# Usage: bash deploy-loom-portal-polish.sh /absolute/path/loom-portal-polish.css
set -euo pipefail
css=${1:-/tmp/loom-portal-polish.css}
[[ -f "$css" ]] || exit 1
compose=/opt/loom-web-deploy/compose.prod.yml
sudo -n test -f "$compose"
baseline=$(sudo -n docker inspect loom-web --format '{{.Image}}')
backup="loom-web:before-portal-polish-$(date -u +%Y%m%dT%H%M%SZ)"
sudo -n docker tag "$baseline" "$backup"
stage=$(mktemp -d /tmp/loom-portal-polish.XXXXXX)
cp "$css" "$stage/loom-portal-polish.css"
sudo -n docker cp loom-web:/srv/loom-web/static/index.html "$stage/index.html"
sudo -n chown "$(id -u):$(id -g)" "$stage/index.html"
python3 - "$stage/index.html" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
html = path.read_text()
html = re.sub(r'\s*<link[^>]+href="/loom-portal-polish\.css[^"\s]*"[^>]*>', '', html)
assert '</head>' in html
html = html.replace('</head>', '  <link rel="stylesheet" href="/loom-portal-polish.css?v=20261001-1">\n</head>')
path.write_text(html)
PY
cat > "$stage/Dockerfile" <<'DOCKER'
ARG BASE_IMAGE=loom-web:independent
FROM ${BASE_IMAGE}
COPY --chown=loomweb:loomweb index.html /srv/loom-web/static/index.html
COPY --chown=loomweb:loomweb loom-portal-polish.css /srv/loom-web/static/loom-portal-polish.css
DOCKER
sudo -n docker build --build-arg "BASE_IMAGE=$backup" -t loom-web:independent "$stage"
sudo -n docker compose -f "$compose" up -d --no-deps --force-recreate loom-web
healthy=false
for attempt in $(seq 1 20); do
  state=$(sudo -n docker inspect loom-web --format '{{.State.Health.Status}}')
  if [[ "$state" == healthy ]]; then healthy=true; break; fi
  sleep 2
done
if [[ "$healthy" != true ]]; then
  sudo -n docker tag "$backup" loom-web:independent
  sudo -n docker compose -f "$compose" up -d --no-deps --force-recreate loom-web
  echo "Health check failed; restored $backup" >&2
  exit 1
fi
printf 'Portal polish deployed. Rollback image: %s\n' "$backup"
