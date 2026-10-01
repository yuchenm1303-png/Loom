# Loom Web Gateway Launch Runbook

Production launch record for `loom.smirel.com`.

## Architecture

```
Browser  --HTTPS/WSS-->  loom.smirel.com  -->  Caddy (termrelay-caddy)
                                              |
                                              +--> loom-web:8790 (auth + WS relay only)
                                                     |
                                                     +--> Loom Desktop
                                                           (over WSS, per-account access token)
                                                     |
                                                     +--> Loom Account Service
                                                           (over HTTPS at account.smirel.com/v1)
```

The gateway itself never executes agent tools. The browser receives only
Secure + HttpOnly cookies. The desktop auth token is sent only over the
desktop-to-gateway WSS channel.

## DNS

| Host             | Type | Value             | TTL   |
| ---------------- | ---- | ----------------- | ----- |
| loom.smirel.com  | A    | 170.106.171.50    | 10 m  |

Other smirel.com subdomains (`account`, `relay`, `power`, `www`, `@`,
`api`, etc.) are unchanged.

## Server-side deployment

The gateway lives on the TermRelay production host `170.106.171.50`
(VM-0-2-ubuntu). It runs in Docker and joins the existing
`termrelay_termrelay-internal` network.

```bash
# Build (run from a checkout of this repo on the host)
docker compose -f /opt/loom-account/services/loom_web_gateway/compose.runtime.yml build loom-web

# Run
docker compose -f /opt/loom-account/services/loom_web_gateway/compose.runtime.yml up -d loom-web
```

The runtime compose uses `/opt/loom-account` as build context so that the
existing `desktop-react/` and `services/loom_web_gateway/` directories in
the workspace are visible to the Dockerfile. The runtime compose file is
generated at deploy time and is not committed to this repository.

## Caddy integration

`/opt/termrelay/deploy/termrelay/Caddyfile` now contains a global options
block that pins Let's Encrypt to the production directory and registers
an email contact, plus a `loom.smirel.com` vhost that reverse-proxies to
`loom-web:8790` inside the `termrelay_termrelay-internal` network.

```caddy
{
    email admin@autohanding.com
    acme_ca https://acme-v02.api.letsencrypt.org/directory
    on_demand_tls {
        ask https://account.smirel.com/v1/tls-allowlist
    }
}

# ... existing {$TERMRELAY_DOMAIN} / alpha / relay / account / power blocks ...

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
```

Caddy reload command:

```bash
docker exec termrelay-caddy caddy reload --config /etc/caddy/Caddyfile --address 127.0.0.1:2019
```

The TermRelay production Caddyfile on the host is **not** committed to this
repository; it lives at `/opt/termrelay/deploy/termrelay/Caddyfile` and is
backed up alongside every modification.

## Verification summary

Run `scripts/loom_target_verify.sh` from a host with network access to
`loom.smirel.com`. The script covers:

1. `https://loom.smirel.com/api/healthz` → `200 {"ok":true}`
2. `https://loom.smirel.com/` → `200` Loom SPA HTML
3. HTTP → HTTPS 308 redirect
4. `/api/ws/browser` (no cookie) → `403 Forbidden` from Caddy
5. `/api/ws/device` (no Authorization) → `403 Forbidden` from Caddy
6. `/api/auth/login` → `401 INVALID_CREDENTIALS` when the account service rejects
7. `/api/auth/status` → `200` snapshot JSON
8. `loom-web` container healthy
9. Caddy routes include `loom.smirel.com`
10. Certificate files present (`CN=loom.smirel.com`, Let's Encrypt prod)
11. SPA build artefacts present under `/app/static/`

## Operational notes

- The gateway never exposes a public port; only Caddy can reach it.
- The gateway runs read-only with `no-new-privileges`.
- Authorization, Cookie, and X-Forwarded-For headers are stripped from
  Caddy access logs on the `loom.smirel.com` vhost.
- WebSocket payload is capped at 64 MiB (`--ws-max-size 67108864`).
- The gateway authenticates the browser via Secure + HttpOnly cookies and
  the desktop via the account access token in the `Authorization` header.
  These two layers cannot cross-pretend because the cookie set and the
  bearer token are stored separately and the WS endpoints validate the
  opposite one as the no-cookie / no-token side.

## Files in this repo

- `services/loom_web_gateway/app.py` — FastAPI + WebSocket relay.
- `services/loom_web_gateway/Dockerfile` — multi-stage build (web bundle +
  Python runtime).
- `services/loom_web_gateway/docker-compose.yml` — reference compose for
  local dev. The production compose is generated on the host.
- `services/loom_web_gateway/requirements.txt` — Python dependencies.
- `services/loom_web_gateway/README.md` — endpoint reference.
## Portal presentation and automatic deployment

The production timer runs `/opt/loom-web-deploy/autosync.sh`, which resets
`/opt/loom-web-main-src` to GitHub `origin/main` and rebuilds the web image.
Portal changes must be committed and pushed to GitHub `main` before publication.
A static-only image overlay will otherwise be replaced by the next main deployment.

The portal uses two offset glass surfaces and the refined Host/account card.
`portal-base.css`, `portal-modules.css`, and `portal-host-card.css` are imported by
`WebPortal.tsx`; the original Smirel glass material and hover/press rules remain
external. Preserve current release metadata and Host connection logic when
restoring presentation changes onto a newer main revision.
