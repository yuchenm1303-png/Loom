#!/usr/bin/env bash
set +e

echo "=== inspect termrelay-caddy TLS state for loom.smirel.com ==="
sudo -n docker exec termrelay-caddy find /data/caddy -type d -name 'loom*' 2>&1 | head
echo
echo "=== Caddy ACME state for loom ==="
sudo -n docker exec termrelay-caddy find /data/caddy -name '*loom*' 2>&1 | head -10
echo
echo "=== Caddy ACME stderr (last 60 lines) ==="
sudo -n docker logs --tail 120 termrelay-caddy 2>&1 | grep -iE 'loom|acme|cert|challenge' | head -60
echo
echo "=== certificate directories ==="
sudo -n docker exec termrelay-caddy find /data/caddy/certificates -type d 2>&1 | head
echo
echo "=== via /etc/hosts (sanity for backend reachability) ==="
sudo -n docker exec termrelay-caddy wget -qO- http://loom-web:8790/api/healthz 2>&1
echo
echo "=== current Caddyfile loom block ==="
sudo -n awk '/^loom.smirel.com/,/^}/' /opt/termrelay/deploy/termrelay/Caddyfile 2>&1
echo
echo "=== confirm Let's Encrypt issuance via acme-v02.api.letsencrypt.org (sanity) ==="
curl -sS --max-time 8 -o /dev/null -w "ACME_root=%{http_code}\n" https://acme-v02.api.letsencrypt.org/directory 2>&1
echo
echo "=== public IP reverse DNS (optional sanity) ==="
sudo -n curl -sS --max-time 5 https://api.ipify.org 2>&1
echo