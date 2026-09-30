# Loom Web

`loom.smirel.com` is an independent Loom Agent runtime that shares the same React renderer
and Python Agent core as Loom Desktop. The browser does **not** require Loom Desktop to be
online for normal conversations, model calls, streaming, cloud-workspace files, approvals,
or agent tools.

The optional Desktop relay remains available only for actions that genuinely need a physical
user device, such as opening the local browser extension or revealing a local path.

## Architecture

```text
Browser -> Loom Web -> per-account loom_app_server.py -> Agent Runtime / cloud workspace
                   \
                    -> optional WSS device relay -> Loom Desktop (local-only actions)
```

Each authenticated Loom account gets a separate runtime home and workspace under
`LOOM_WEB_RUNTIME_ROOT`. Those directories live on the `loom_web_data` volume so Web
conversations survive container restarts. Desktop and Web are separate runtimes and do not
share process lifetime or local files.

## Runtime model configuration

Production Loom Web uses a server-managed Muxway Relay credential. The credential never
reaches browser JavaScript. The Web runtime reads the Relay `/models` catalog, exposes every
model authorized for that credential, and can switch models independently from Loom Desktop.

Configure the deployment with:

- `LOOM_WEB_BASE_URL` — Relay endpoint, defaults to `https://muxway.dev/v1`
- `LOOM_WEB_MODEL` — preferred/default model, defaults to `MiniMax-M3`
- `LOOM_WEB_API_KEY` — dedicated Loom Web Relay credential
- `LOOM_WEB_PERMISSION_MODE` — defaults to `workspace`

`LOOM_WEB_API_KEY_FILE` is preferred in production so the Relay credential can be mounted as
a read-only secret instead of being written into Compose or browser-visible configuration.
The selected Web model is persisted per Loom account inside `loom_web_data`; Desktop model
selection remains independent.

## Endpoints

- `GET /api/healthz`
- `GET|POST /api/auth/*` — same-origin account facade with Secure HttpOnly cookies
- `WS /api/ws/browser` — browser <-> independent Web runtime
- `WS /api/ws/device` — optional outbound Loom Desktop connection

The device socket requires the Loom Account access token in the `Authorization` header.
The browser never receives that token; it uses Secure, HttpOnly, SameSite=Strict cookies.

## Caddy

```caddy
loom.smirel.com {
    reverse_proxy loom-web:8790
}
```

Attach `loom-web` to the same Docker network as Caddy and keep it without a public host port.
The Desktop device relay, when used, connects outbound to `wss://loom.smirel.com/api/ws/device`.