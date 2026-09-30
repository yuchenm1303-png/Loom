#!/usr/bin/env bash
set +e

echo "=== check the FIRST line is the {$TERMRELAY_DOMAIN} placeholder ==="
head -3 /opt/termrelay/deploy/termrelay/Caddyfile
echo
echo "=== ENV var TERMRELAY_DOMAIN ==="
sudo -n docker exec termrelay-caddy sh -c 'echo TERMRELAY_DOMAIN=$TERMRELAY_DOMAIN'
echo
echo "=== restart termrelay-caddy ==="
sudo -n docker restart termrelay-caddy 2>&1
echo
sleep 5
echo
echo "=== check post-restart log ==="
sudo -n docker logs --tail 60 termrelay-caddy 2>&1 | grep -iE 'loom|obtain|staging' | tail -40
echo
echo "=== admin /config/ after restart ==="
sudo -n docker exec termrelay-caddy wget -qO- http://127.0.0.1:2019/config/ 2>&1 > /tmp/cfg.json
grep -oE '"host":\["[^"]+"\]' /tmp/cfg.json | sort -u
echo
echo "=== public curl to loom.smirel.com ==="
curl -sS -o /tmp/curl.out -w "code=%{http_code} time=%{time_total} size=%{size_download}\n" --connect-timeout 8 --max-time 15 https://loom.smirel.com/api/healthz 2>&1
head -c 200 /tmp/curl.out 2>/dev/null
echo
echo "=== public curl root ==="
curl -sS -o /tmp/root.html -w "code=%{http_code} size=%{size_download}\n" --connect-timeout 8 --max-time 15 https://loom.smirel.com/ 2>&1
head -c 300 /tmp/root.html 2>/dev/null
echo
echo "=== HTTP -> HTTPS redirect ==="
curl -sS -I -o /tmp/redir.txt --connect-timeout 8 --max-time 15 http://loom.smirel.com/api/healthz 2>&1 | head -10
echo
echo "=== WebSocket upgrade probe ==="
curl -sS -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGVzdA==" \
  --connect-timeout 8 --max-time 12 \
  https://loom.smirel.com/api/ws/browser 2>&1 | head -15