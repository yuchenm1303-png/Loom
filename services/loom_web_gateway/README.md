# Loom Web

`loom.smirel.com` is the browser client for the same **local Loom Host** used by Loom Desktop.
There is only one Agent runtime per computer and one source of truth on that computer for
conversations, model settings, approvals, workspace files, Browser Use, Computer Use and
local tools.

The cloud gateway does **not** run an Agent. It only serves the shared React frontend,
authenticates the Loom account, and relays WebSocket traffic between a browser and the
specific Loom Host that browser selected.

## Local-first product rule

Normal Loom usage is local-first:

- opening Loom Web from Loom Desktop binds that browser to **this computer's** stable
  `deviceId`;
- the browser stores that local binding and reconnects to the same Host on later visits;
- signing in to `loom.smirel.com` without a local binding does **not** pick another online
  computer on the account;
- another computer may only be selected by an explicit remote-control flow (Loom Remote).

This is intentionally different from treating the account as one global remote Host. A user
who opens Loom on computer B should never discover that commands were silently executed on
computer A.

## Architecture

```text
computer A                                  cloud                    computer B
+----------------------+              +----------------+          +----------------------+
| Desktop React        |              | loom.smirel.com|          | Desktop React        |
| Loom Host / AppServer|<--- outbound | auth + WSS     | outbound->| Loom Host / AppServer|
| Agent / files / tools|      WSS     | relay only     |     WSS  | Agent / files / tools|
+----------------------+              +-------+--------+          +----------------------+
                                             ^
                                             |
                                      browser session
                                      bound to one deviceId
```

Desktop and Web use the same `desktop-react` renderer and the same host operation table.
The transport is the only difference:

- Desktop: React -> Electron IPC -> local Loom Host
- Web: React -> WSS gateway -> explicitly bound Loom Host

Closing the Desktop window does not stop Loom Host. On Windows the installed app registers a
background-host login launch and keeps a tray entry so Web access remains available without
an Electron window being open. The tray provides **Open Loom**, **Open Loom Web**, and
**Quit Loom Host**. **Open Loom Web** includes this computer's `deviceId` in the initial URL;
the browser consumes it into local storage and removes it from the visible URL.

## Multi-device routing

A Loom account can have multiple online Hosts at the same time. The gateway stores devices as:

```text
user_id -> device_id -> DevicePeer
browser_id -> selected_device_id
```

A newer connection replaces only an older connection with the **same** `deviceId`; it does
not disconnect the user's other computers. Invocations and runtime notifications are routed
only between a browser and its selected device. This keeps streaming deltas, approvals and
turn completion from leaking across two computers logged into the same account.

The browser sends its explicit device binding in the WebSocket URL. Switching devices
opens a new connection so pending calls and notifications cannot cross execution targets.
An absent or offline binding never falls back to another online Host.

## Security boundary

- Browser authentication uses Secure, HttpOnly, SameSite=Strict cookies.
- Each local Host connects **outbound** to `wss://loom.smirel.com/api/ws/device`; no inbound
  port is opened on the user's computer.
- The Host authenticates with the user's Loom Account access token in the WSS Authorization
  header. Browser JavaScript never receives that token.
- The gateway only connects browser and Host peers belonging to the same authenticated user.
- Each handshake issues a separate, hashed-at-rest relay credential bound to the originating
  account session. The gateway rechecks it every 30 seconds (with a 15-second account HTTP
  timeout) and before sending when its validation window has elapsed. Normal access-token
  rotation keeps the connection authorized; session revocation, account disablement, expiry
  or a failed recheck closes it. The proof never grants access to account or model APIs.
- Relay sends have a 10-second bound, and each browser is limited to 32 pending invocations.
- Browser token refresh is shared across concurrent requests for a short overlap window.
  Account outages do not rotate cookies or report the service as reachable.
- A local `deviceId` is a routing identifier, not an authentication secret; account auth still
  gates every browser and Host connection.
- Operation names are allow-listed by the Host's shared `desktopOperations` table.
- App Server approvals, sandboxing and permission checks are unchanged because Web invokes
  the exact same local App Server as Desktop.

## Endpoints

- `GET /api/healthz`
- `GET|POST /api/auth/*` — same-origin account facade
- `WS /api/ws/browser?device=<deviceId>` — authenticated browser transport bound to one Host
- `WS /api/ws/device` — outbound local Loom Host transport
- `GET /setup` — retired; returns 410 because Web no longer has a cloud model credential

## Caddy

```caddy
loom.smirel.com {
    reverse_proxy loom-web:8790
}
```

Attach `loom-web` to the same internal Docker network as Caddy. It needs no public host port,
no model API key, no workspace volume, and no server-side Loom Agent runtime.

Deploy the account service with `/v1/auth/relay-credential` and `/v1/auth/relay-me` before
deploying the updated gateway. Schema initialization adds `relay_credentials` automatically
without replacing existing users or sessions. This in-memory routing hub requires one
gateway worker/instance; multiple replicas need shared routing before they are supported.
