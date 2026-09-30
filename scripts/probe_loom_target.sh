#!/usr/bin/env bash
set +e

# All docker calls use sudo -n (NOPASSWD) confirmed
echo "=== sudo docker ps ==="
sudo -n docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
echo "=== sudo docker network ls ==="
sudo -n docker network ls --format 'table {{.Name}}\t{{.Driver}}\t{{.Scope}}'
echo "=== sudo docker compose ls ==="
sudo -n docker compose ls 2>&1 | head -20
echo "=== termrelay-caddy mounts ==="
sudo -n docker inspect termrelay-caddy --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Type}}{{if .Mode}},mode={{.Mode}}{{end}}) rw={{.RW}}\n{{end}}'
echo "=== termrelay-caddy Caddyfile (length + first 150 lines) ==="
sudo -n docker exec termrelay-caddy wc -c /etc/caddy/Caddyfile 2>&1
sudo -n docker exec termrelay-caddy sed -n '1,150p' /etc/caddy/Caddyfile 2>&1
echo "=== termrelay-caddy vhost lines ==="
sudo -n docker exec termrelay-caddy grep -nE '^[a-zA-Z0-9_.-]+ ' /etc/caddy/Caddyfile 2>&1
echo "=== termrelay-caddy caddy version ==="
sudo -n docker exec termrelay-caddy caddy version 2>&1
echo "=== termrelay-caddy data dir ==="
sudo -n docker exec termrelay-caddy ls -la /data 2>&1
echo "=== HTTP probes ==="
curl -sS -o - --max-time 8 https://account.smirel.com/v1/healthz 2>&1
echo
curl -sS -o - --max-time 8 https://account.smirel.com/ 2>&1
echo
curl -sS -o - --max-time 8 https://account.smirel.com/v1/auth/login 2>&1 | head -5
echo
curl -sS -o - --max-time 8 https://relay.smirel.com/ 2>&1 | head -5
echo
curl -sS -o - --max-time 8 https://relay.smirel.com/api/healthz 2>&1
echo
echo "=== /opt/loom-account tree ==="
ls -laR /opt/loom-account 2>&1 | head -60
echo "=== /opt/termrelay/deploy ==="
ls -la /opt/termrelay/deploy 2>&1 | head -20
echo "=== sudo docker ps -a ==="
sudo -n docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'