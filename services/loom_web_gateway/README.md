# Loom Web Gateway

`loom.smirel.com` uses the existing React renderer and the existing Loom Desktop runtime.
The gateway never executes agent tools itself. It authenticates browser/device sessions and
relays RPC frames over WebSocket to the signed-in desktop, so the desktop App Server remains
the authority for approvals, sandboxing, Computer Use, Browser Use, files and model calls.

## Endpoints

- `GET /api/healthz`
- `GET|POST /api/auth/*` — same-origin account facade with Secure HttpOnly cookies
- `WS /api/ws/browser` — browser renderer
- `WS /api/ws/device` — outbound Loom Desktop connection

The device socket requires the Loom Account access token in the `Authorization` header.
The browser never receives that token; it uses Secure, HttpOnly, SameSite=Strict cookies.

## Caddy

```caddy
loom.smirel.com {
    reverse_proxy loom-web:8790
}
```

Attach `loom-web` to the same Docker network as Caddy and keep the gateway without a public
host port. The desktop always connects outbound to `wss://loom.smirel.com/api/ws/device`.
