# Deploying the Loom Account Service

The account service is a single-file Python standard-library HTTP server over
SQLite. It has no dependencies to install, no database server to run, and no
external service to call. That shapes everything below.

## Why this is self-hosted and not Supabase

The service was deliberately built to be independent of any relay, third-party
API service, or model provider, and the desktop client enforces part of that:
it rejects plain-HTTP account endpoints unless they are loopback. Handing
identity to a hosted provider would mean:

- rewriting `services/loom_account/server.py` and the token contract in
  `desktop-react/electron/accountClient.ts`, because the service issues rotating
  opaque access/refresh token pairs rather than provider JWTs;
- making desktop sign-in depend on a third party's uptime and free-tier
  policies, where a paused project means nobody can sign in;
- storing user credentials and sessions with an external operator, which is the
  opposite of what the service was designed for.

The resource argument for outsourcing also does not hold. Measured on the
reference host, an equivalent Python container uses roughly 25 MB of memory and
effectively no CPU. The service is not what makes a small host feel small.

If you later need a shared identity across products, the right move is to point
Loom at an existing internal account backend rather than to add a third-party
identity provider. Standing up a second, parallel account store is the outcome
to avoid.

## Reference deployment

Live and verified:

| | |
| --- | --- |
| Public URL | `https://account.smirel.com` → `170.106.171.50` (Tencent Cloud, Ubuntu 24.04) |
| Desktop base URL | `https://account.smirel.com/v1` |
| Host directory | `/opt/loom-account/` |
| Image / container | `loom-account:local` / `loom-account` (pinned via `name:` + `container_name:`) |
| Listening on | `127.0.0.1:8787` (not published publicly) |
| Network | `termrelay_termrelay-internal`, alongside the existing `termrelay-caddy` |
| Proxy | the existing Caddy vhost added to `/opt/termrelay/deploy/termrelay/Caddyfile` |
| Trusted proxies | `127.0.0.1,::1,172.18.0.0/16` — the CIDR is required because Caddy reaches the service over the Docker bridge, not loopback |
| Footprint | ~32 MiB RSS, ~0.01% CPU |

Verified over the public endpoint: 26/26 checks covering the security response
headers, the full register → me → refresh → rotate → logout flow, the error-code
contract, and rate limiting. Rate limiting was confirmed to be **per client**,
not global: while one address was throttled to `429`, a different address still
received `401`. The vhost's access log was confirmed to contain zero occurrences
of `Authorization` or `Cookie`, and the only paths ever requested were `/healthz`
and the `/v1/auth/*` endpoints — no credential has ever travelled in a URL.

The host is on the public internet and is already being scanned: the log shows
the usual background noise (`/.env`, `/.git/config`, `/actuator/env`,
`/v2/_catalog`, `/wp/v2/users`, …). All of it returned `404`; there is no admin
or debug surface to find.

`alpha.smirel.com` has a Caddy vhost on this host but no DNS record, so it
resolves nowhere; `relay.smirel.com` returns `502` because the TermRelay
containers are stopped. Neither is related to the account service.

## Deploy

```bash
cd services/loom_account/deploy
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8787/healthz   # {"ok":true}
```

The database lives in the `loom-account-data` volume. To keep it somewhere you
back up, replace the named volume with a bind mount and make sure the directory
is writable by uid `10001` — the image runs unprivileged:

```yaml
    volumes:
      - /srv/loom-account:/data
```

```bash
sudo install -d -o 10001 -g 10001 /srv/loom-account
```

## Put it behind TLS

Append `Caddyfile` from this directory to the Caddyfile of the proxy that
already serves your domain, set `LOOM_ACCOUNT_DOMAIN`, and reload Caddy. Then
verify from outside the host:

```bash
curl -s https://account.example.com/v1/auth/me   # 401 MISSING_TOKEN
```

A `401` with that error code is the correct answer: it means TLS, routing and
the service are all working.

## Point the desktop at it

Per machine, preferred:

```json
{ "baseUrl": "https://account.example.com/v1" }
```

Saved as `~/.loom/account-service.json`. Or set `LOOM_ACCOUNT_API_BASE_URL`,
which takes precedence. Packaged builds only accept `https:` here, except for
loopback.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `LOOM_ACCOUNT_DB` | `~/.loom-account/accounts.db` | `/data/accounts.db` in the image |
| `LOOM_ACCOUNT_HOST` / `LOOM_ACCOUNT_PORT` | `127.0.0.1` / `8787` | `0.0.0.0` in the image, bound to loopback by the published port |
| `LOOM_ACCOUNT_ACCESS_TTL` | `900` | Access token lifetime, seconds |
| `LOOM_ACCOUNT_REFRESH_TTL` | `2592000` | Refresh token lifetime, seconds |
| `LOOM_ACCOUNT_TRUSTED_PROXIES` | `127.0.0.1,::1` | Peers allowed to set the client address via `X-Real-IP` / `X-Forwarded-For` |
| `LOOM_ACCOUNT_QUIET` | unset | `1` silences per-request access logging |

## Operational notes

**Rate limiting depends on the proxy configuration.** Limits are applied per
client address. Behind a proxy, every request arrives from the proxy, so the
service believes `X-Real-IP` / `X-Forwarded-For` — but only when the immediate
peer is listed in `LOOM_ACCOUNT_TRUSTED_PROXIES`. Get this wrong in either
direction and it breaks quietly:

- proxy not trusted → every client in the world shares one bucket, and one
  attacker locks out everyone;
- proxy trusted but not overwriting the header → callers pick their own bucket
  and the limit does nothing.

The Caddyfile here overwrites both headers, which is what makes trusting the
proxy safe. If you front the service with something else, confirm it overwrites
rather than appends.

**Keep credentials out of the access log.** Two separate things are true here,
and conflating them leads to a false sense of security:

*Header values.* Caddy redacts `Authorization` and `Cookie` on its own — the
other vhosts on the reference host run a plain `format console` and still emit
`"Authorization": ["REDACTED"]`. The `filter` encoder in the Caddyfile here is
defence in depth, not a fix for a Caddy default: it removes the key entirely
instead of leaving a `REDACTED` placeholder, and it keeps holding if a future
Caddy release changes its built-in redaction list. nginx is the opposite — it
logs `$http_authorization` only if you ask for it.

*The request URI.* Caddy does **not** redact this, so any credential passed as a
query parameter is written to the log verbatim. This service never does that —
credentials travel in the JSON body or the `Authorization` header, and the
production logs show only `/healthz` and `/v1/auth/*` were ever requested. Treat
that as a design constraint, not an accident: an endpoint that accepts a token in
the URL turns the access log into a credential store.

A related trap on the same host: `relay.smirel.com` and `api.smirel.com` proxy a
console whose OAuth callback carries `?code=...&state=...`. Those values *do* end
up in the log in plaintext. They are single-use and short-lived, so the practical
exposure is small, but if you ever need to close it, the fix belongs in the
application (exchange the code over POST), not in a URI-deleting log filter that
would also destroy the log's routing value.

**Single instance only.** SQLite is the store, so one container is the supported
topology. A second replica would hold a separate and divergent set of accounts.
Migrate the storage adapter to a shared database before scaling out.

**Back up the database.** Everything that matters is in `accounts.db`. With WAL
enabled, copy `accounts.db`, `accounts.db-wal` and `accounts.db-shm` together,
or use `sqlite3 accounts.db ".backup"`.

**Token semantics worth knowing before you debug.** Refresh tokens rotate on
every use, and rotating also invalidates the access token issued alongside the
spent refresh token. A client that refreshes twice concurrently will therefore
lose one of the two sessions; that is intended, not a bug.

## Verify a deployment end to end

```bash
BASE=https://account.example.com/v1
curl -s -X POST "$BASE/auth/register" -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"correct-horse-battery"}'
```

Then confirm rotation and revocation behave:

```bash
# The refresh token from the step above
curl -s -X POST "$BASE/auth/refresh" -H 'Content-Type: application/json' \
  -d '{"refresh_token":"loom_refresh_..."}'          # 200, new pair
curl -s -X POST "$BASE/auth/refresh" -H 'Content-Type: application/json' \
  -d '{"refresh_token":"loom_refresh_..."}'          # 401 INVALID_REFRESH_TOKEN
```

The automated suites covering this are `tests/test_loom_account_service.py`
(application level) and `tests/test_loom_account_http.py` (real HTTP server,
routing, status codes, rate limiting, request limits).
