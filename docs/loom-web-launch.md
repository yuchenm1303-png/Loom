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
## Modular portal layout (2026-10-01)

The active production container uses `/opt/loom-web-deploy/compose.prod.yml`
and `loom-web:independent`. The matching frontend source is under
`/opt/loom-web-main-src/desktop-react`, rather than the legacy account checkout.
The portal now separates introduction, download, Host status, and account/device
information into modules. `portal-base.css` captures the production portal's
existing layout rules; `portal-modules.css` changes only layout and typography.
Every module inherits the original externally provided `.cards` material,
hover, press, and transition rules. Keep entry animation on individual cards:
a filter animation on their parent prevents backdrop blur from sampling the
wallpaper correctly.

Build the matching production source with the updated `WebPortal.tsx`,
`portal-base.css`, and `portal-modules.css`, then pass the absolute built `dist`
path to `scripts/deploy-loom-portal-layout.sh`. It layers static files over the
running image, retains existing asset files, saves a rollback tag, checks health,
and leaves the gateway and its environment intact. Credentials stay on the host.
The local WebAppGate also renders WebPortal and retains its existing Host logic.

Validated: frontend type check/build, public health endpoint, signed-in and
signed-out browser layouts at 1440/900/390px, register/password controls, and
original hover/press transforms. Auth checks use a mocked account; no real
credentials or Host operations are required for presentation verification.

### Composition revision

The portal now uses two unequal, vertically offset glass surfaces. Introduction
and download share the main surface; Host status and account/device details
share the smaller side surface. Account height follows its content. The headline
uses sans-serif and serif italic lines, with the original words preserved.
This avoids stretching sparse account information into an oversized card.
Surface styles and original hover/press rules remain supplied by Smirel.

Verified signed-in and signed-out layouts at 2557, 1920, 1440, 900 and 390px,
plus register/password controls, original hover/press transforms, and healthz.

The wide-screen composition no longer caps the shell at 1180px. At 1440,
1920, and 2557px, the shell occupies about 91% of the viewport width. Headline,
body copy, side-panel text, gutters, and padding scale with viewport size;
mobile overrides retain the single-column layout. The glass surface and original
hover/press behavior remain unchanged. Build and responsive checks passed.

### Automatic deployment source

The production timer runs `/opt/loom-web-deploy/autosync.sh`, which resets
`/opt/loom-web-main-src` to GitHub `origin/main` and rebuilds the web image.
Portal changes must be committed and pushed to GitHub `main` before publication.
A static-only image overlay will otherwise be replaced by the next main deployment.

The refined Host card also imports `portal-host-card.css`. Preserve current
release metadata and Host connection fixes when restoring presentation changes
onto a newer main revision. The restored presentation was published from an
isolated checkout of current GitHub main to avoid deploying unrelated local commits.
