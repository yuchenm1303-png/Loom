#!/usr/bin/env bash
# Deploy Loom Web Gateway on VM-0-2-ubuntu.
set +e

WS=/opt/loom-account
CF=/opt/termrelay/deploy/termrelay/Caddyfile

echo "=== 0) sanity ==="
uptime
df -h /
sudo -n docker --version
sudo -n docker compose version

echo
echo "=== 1) verify synced tree ==="
ls -la "$WS/services/loom_web_gateway"
ls -la "$WS/desktop-react" | head

echo
echo "=== 2) write loom_web gateway env file ==="
mkdir -p "$WS/services/loom_web_gateway"
cat > "$WS/services/loom_web_gateway/.env" <<ENV
LOOM_ACCOUNT_API_BASE_URL=https://account.smirel.com/v1
LOOM_EDGE_NETWORK=termrelay_termrelay-internal
LOOM_WEB_ORIGIN=https://loom.smirel.com
ENV
chown -R ubuntu:ubuntu "$WS/services/loom_web_gateway"
ls -la "$WS/services/loom_web_gateway/.env"

echo
echo "=== 3) backup Caddyfile if loom.smirel.com absent ==="
if grep -q '^loom\.smirel\.com' "$CF"; then
  echo "[skip-append] loom.smirel.com already present"
  BAK="$(ls -t "${CF}.bak."* 2>/dev/null | head -n1)"
  [ -z "$BAK" ] && BAK="$CF"
else
  BAK="${CF}.bak.$(date +%Y%m%d-%H%M%S)"
  sudo -n cp -p "$CF" "$BAK"
  echo "backup -> $BAK"
  cat >> "$CF" <<'CADDY'

# --- Loom Web Gateway (loom.smirel.com) ---
# Authentication + WebSocket relay only. No agent tools execute here.
loom.smirel.com {
    encode zstd gzip
    reverse_proxy loom-web:8790 {
        flush_interval -1
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}
    }
    header {
        -Server
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
    }
    log {
        output stdout
        format filter {
            wrap console
            fields {
                request>headers>Authorization delete
                request>headers>Cookie delete
                request>headers>X-Forwarded-For delete
            }
        }
    }
}
CADDY
fi

echo
echo "=== 4) caddy validate ==="
sudo -n docker exec -w /etc/caddy termrelay-caddy caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -20

echo
echo "=== 5) reload caddy (only if validate ok) ==="
sudo -n docker exec termrelay-caddy caddy reload --config /etc/caddy/Caddyfile --address 127.0.0.1:2019 2>&1
sleep 2
sudo -n docker logs --tail 40 termrelay-caddy 2>&1 | tail -40

echo
echo "=== 6) write a build-context-rooted compose that uses /opt/loom-account as context ==="
mkdir -p "$WS/services/loom_web_gateway"
cat > "$WS/services/loom_web_gateway/compose.runtime.yml" <<YML
services:
  loom-web:
    build:
      context: ${WS}
      dockerfile: services/loom_web_gateway/Dockerfile
    image: loom-web:local
    container_name: loom-web
    restart: unless-stopped
    env_file:
      - ${WS}/services/loom_web_gateway/.env
    networks:
      - edge
    security_opt:
      - no-new-privileges:true
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8790/api/healthz', timeout=3).status == 200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 60s

networks:
  edge:
    external: true
    name: termrelay_termrelay-internal
YML
ls -la "$WS/services/loom_web_gateway/compose.runtime.yml"

echo
echo "=== 7) build loom-web image ==="
cd "$WS"
sudo -n docker compose -f services/loom_web_gateway/compose.runtime.yml build --pull=false loom-web 2>&1 | tail -30

echo
echo "=== 8) ensure loom-web not already running ==="
sudo -n docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

echo
echo "=== 9) bring up loom-web ==="
sudo -n docker compose -f services/loom_web_gateway/compose.runtime.yml up -d loom-web 2>&1 | tail -20

echo
echo "=== 10) final state ==="
sleep 4
sudo -n docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
echo
echo "=== 11) healthz probe (from inside termrelay-caddy network) ==="
sudo -n docker exec termrelay-caddy wget -qO- http://loom-web:8790/api/healthz 2>&1 | head -3 || true
echo
echo "=== 12) loom-web logs ==="
sudo -n docker logs --tail 40 loom-web 2>&1 | tail -40