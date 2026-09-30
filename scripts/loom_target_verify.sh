#!/usr/bin/env bash
set +e

echo "=== FULL VERIFICATION for loom.smirel.com ==="
echo

echo "### 1. https://loom.smirel.com /api/healthz"
curl -sS --connect-timeout 8 --max-time 20 -o /tmp/h.json -w "code=%{http_code} time=%{time_total} size=%{size_download}\n" https://loom.smirel.com/api/healthz
echo -n "body="; cat /tmp/h.json; echo

echo
echo "### 2. https://loom.smirel.com/ (SPA)"
curl -sS --connect-timeout 8 --max-time 20 -o /tmp/index.html -w "code=%{http_code} size=%{size_download}\n" https://loom.smirel.com/
head -c 600 /tmp/index.html

echo
echo
echo "### 3. HTTP -> HTTPS redirect"
curl -sS -I --connect-timeout 8 --max-time 20 http://loom.smirel.com/api/healthz | head -8

echo
echo "### 4. /api/ws/browser (no cookie -> expect 4401)"
curl -sS -i -N --connect-timeout 8 --max-time 12 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGVzdA==" \
  https://loom.smirel.com/api/ws/browser 2>&1 | head -20

echo
echo "### 5. /api/ws/device (no token -> expect 4401)"
curl -sS -i -N --connect-timeout 8 --max-time 12 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGVzdA==" \
  https://loom.smirel.com/api/ws/device 2>&1 | head -20

echo
echo "### 6. /api/auth/login (invalid creds -> 401/4xx)"
curl -sS -X POST --connect-timeout 8 --max-time 12 \
  -H "Content-Type: application/json" \
  -d '{"email":"probe@invalid.local","password":"x"}' \
  https://loom.smirel.com/api/auth/login 2>&1 | head -3

echo
echo "### 7. /api/auth/status (no cookie)"
curl -sS --connect-timeout 8 --max-time 12 https://loom.smirel.com/api/auth/status
echo

echo
echo "### 8. loom-web container health"
sudo -n docker ps --filter name=loom-web --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

echo
echo "### 9. Caddy routes"
sudo -n docker exec termrelay-caddy wget -qO- http://127.0.0.1:2019/config/ 2>&1 | grep -oE '"host":\["[^"]+"\]' | sort -u

echo
echo "### 10. cert files"
sudo -n docker exec termrelay-caddy ls -la /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/loom.smirel.com/ 2>&1

echo
echo "### 11. SPA build artefacts"
sudo -n docker exec loom-web ls -la /app/static/ | head -20

echo
echo "### 12. loom-web log tail"
sudo -n docker logs --tail 30 loom-web 2>&1 | tail -20

echo
echo "### 13. certificate peer info"
echo | openssl s_client -servername loom.smirel.com -connect loom.smirel.com:443 2>/dev/null | openssl x509 -noout -subject -issuer -dates 2>&1