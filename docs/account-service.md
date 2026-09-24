# Loom Account Service

This is Loom's standalone account backend. It is intentionally independent from any relay, third-party API service, or model provider.

## Scope

The first account-service version provides:

- email/password registration
- email/password login
- short-lived access tokens
- rotating refresh tokens
- current-user lookup
- logout/session revocation
- per-IP authentication rate limits
- PBKDF2-SHA256 password hashing
- hashed server-side token storage

The desktop renderer never receives access or refresh tokens. Electron main keeps the session and encrypts it with `safeStorage`.

## Run locally

```bash
python -m services.loom_account.server --host 127.0.0.1 --port 8787
```

The development desktop defaults to:

```text
http://127.0.0.1:8787/v1
```

## Deployed instance

Loom's own deployment is live at **`https://account.smirel.com`**; the desktop
base URL is `https://account.smirel.com/v1`. It runs as an unprivileged
container on the Loom-owned host, published only on loopback and reached
through the Caddy instance that already terminates TLS there. Configuration and
operational notes: [`services/loom_account/deploy/README.md`](../services/loom_account/deploy/README.md).

## Deploy independently

The service is designed to run on a Loom-owned host. Do not deploy it onto an unrelated relay or third-party server.

Ready-made deployment files live in [`services/loom_account/deploy`](../services/loom_account/deploy/README.md): a `docker-compose.yml` (loopback-bound, health-checked, unprivileged) and a Caddy vhost that terminates TLS. Prefer those over hand-rolled `docker run`. That README also documents the proxy/rate-limit interaction, which fails quietly if the trusted-proxy setting does not match the deployment, and the single-instance limit.

Build the standalone image:

```bash
docker build -f services/loom_account/Dockerfile -t loom-account .
docker run --rm \
  -p 127.0.0.1:8787:8787 \
  -v loom-account-data:/data \
  loom-account
```

Put the service behind an HTTPS reverse proxy. The desktop client rejects remote plain-HTTP account endpoints; HTTP is accepted only for loopback development.

Recommended public layout:

```text
https://account.<your-loom-domain>/v1
        |
        +-- reverse proxy / TLS
        |
        +-- 127.0.0.1:8787
```

## Configure the desktop

Preferred per-machine configuration:

```json
{
  "baseUrl": "https://account.example.com/v1"
}
```

Save it as:

```text
~/.loom/account-service.json
```

Or set:

```text
LOOM_ACCOUNT_API_BASE_URL=https://account.example.com/v1
```

The environment variable takes precedence.

## Storage

By default the service stores its database at:

```text
~/.loom-account/accounts.db
```

The Docker image uses:

```text
/data/accounts.db
```

Override with `LOOM_ACCOUNT_DB`.

## Security notes

- Passwords are never stored in plaintext.
- Raw access/refresh tokens are never stored server-side.
- Refresh tokens rotate atomically on use.
- Desktop tokens are never exposed to the React renderer.
- Packaged Loom only accepts HTTPS account endpoints, except localhost.
- The built-in SQLite store is appropriate for a single service instance. Before horizontally scaling the account service, migrate the storage adapter to PostgreSQL or another shared transactional database.
