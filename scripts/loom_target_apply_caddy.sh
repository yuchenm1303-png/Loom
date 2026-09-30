#!/usr/bin/env bash
set +e

CF=/opt/termrelay/deploy/termrelay/Caddyfile

echo "=== backup Caddyfile ==="
BAK="${CF}.bak.$(date +%Y%m%d-%H%M%S)"
sudo -n cp -p "$CF" "$BAK"
ls -la "$BAK"

echo
echo "=== write a new Caddyfile that prepends a global options block with explicit prod CA + email ==="
# Note: the existing Caddyfile currently starts directly with "{$TERMRELAY_DOMAIN} {". Caddy global
# options MUST be the FIRST block in the file, with curly braces and a leading empty global.
# We replace the file with a composed form:
#   1) a global options block { email ... acme_ca https://acme-v02.api.letsencrypt.org/directory }
#   2) the original {$TERMRELAY_DOMAIN} block unchanged (placeholder + body)
#   3) the original alpha / relay / account / power blocks unchanged
#   4) the loom.smirel.com block we appended earlier
# Strategy: read original file, find the line "{$TERMRELAY_DOMAIN} {", split at top,
# prepend global block + leading newline.
HEAD_BLOCK=""
TAIL_BLOCK=""
{
  IFS= read -r LINE
  while [ -n "$LINE" ]; do
    if [ "$LINE" = '{$TERMRELAY_DOMAIN} {' ]; then
      printf '%s\n' "$LINE"
      break
    fi
    HEAD_BLOCK="$HEAD_BLOCK$LINE"$'\n'
    IFS= read -r LINE || LINE=""
  done
  while IFS= read -r LINE; do
    printf '%s\n' "$LINE"
  done
} < "$CF" > /tmp/caddy_tail.tmp

# If head_block is empty, that means file already started with placeholder; that's fine.
# Compose new file:
{
  printf '%s\n' "{"
  printf '%s\n' '    email admin@autohanding.com'
  printf '%s\n' '    acme_ca https://acme-v02.api.letsencrypt.org/directory'
  printf '%s\n' "    on_demand_tls {"
  printf '%s\n' '        ask https://account.smirel.com/v1/tls-allowlist'
  printf '%s\n' '    }'
  printf '%s\n' '}'
  printf '%s\n' ""
  # Then add head_block (lines BEFORE the placeholder, usually empty)
  if [ -n "$HEAD_BLOCK" ]; then
    printf '%s' "$HEAD_BLOCK"
  fi
  # Then placeholder + rest
  cat /tmp/caddy_tail.tmp
} > /tmp/Caddyfile.new

echo "=== diff before / after ==="
diff "$BAK" /tmp/Caddyfile.new | head -50
echo

echo "=== write back to host Caddyfile ==="
sudo -n cp /tmp/Caddyfile.new "$CF"
ls -la "$CF"

echo
echo "=== validate via caddy ==="
sudo -n docker exec -w /etc/caddy termrelay-caddy caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -10
echo
echo "=== restart termrelay-caddy to pick up global options ==="
sudo -n docker restart termrelay-caddy 2>&1
sleep 5
echo
echo "=== Caddy startup logs ==="
sudo -n docker logs --tail 80 termrelay-caddy 2>&1 | grep -iE 'loom|acme|staging|on_demand|email' | tail -30
echo
echo "=== Caddy certificate acquisition for loom ==="
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  status=$(sudo -n docker exec termrelay-caddy ls /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/loom.smirel.com 2>/dev/null)
  if [ -n "$status" ]; then
    echo "loom cert ready after ${i}s"
    break
  fi
  sleep 1
done

echo
echo "=== final cert dir ==="
sudo -n docker exec termrelay-caddy ls -la /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/loom.smirel.com/ 2>&1
echo
echo "=== public curl to loom.smirel.com ==="
curl -sS -o /tmp/curl.out -w "code=%{http_code} time=%{time_total} size=%{size_download}\n" --connect-timeout 8 --max-time 20 https://loom.smirel.com/api/healthz 2>&1
head -c 200 /tmp/curl.out 2>/dev/null
echo
echo "=== public curl root ==="
curl -sS -o /tmp/root.html -w "code=%{http_code} size=%{size_download}\n" --connect-timeout 8 --max-time 20 https://loom.smirel.com/ 2>&1
head -c 600 /tmp/root.html 2>/dev/null
echo
echo "=== HTTP -> HTTPS redirect ==="
curl -sS -I --connect-timeout 8 --max-time 20 http://loom.smirel.com/api/healthz 2>&1 | head -10