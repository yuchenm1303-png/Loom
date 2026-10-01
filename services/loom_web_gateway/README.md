# Loom Web

`loom.smirel.com` is the browser client for the same **local Loom Host** used by Loom Desktop.
There is only one Agent runtime and one source of truth for conversations, model settings,
approvals, workspace files, Browser Use, Computer Use and local tools.

The cloud gateway does **not** run an Agent. It only serves the shared React frontend,
authenticates the Loom account, and relays WebSocket traffic between the browser and the
user's local Loom Host.

## Architecture

```text
                       local computer
                  +----------------------+
Desktop React ---->  Loom Host / App Server  <---- WSS relay ---- Browser React
                  | Agent Runtime        |                     loom.smirel.com
                  | models / sessions    |
                  | shell / files        |
                  | Browser / Computer   |
                  | approvals / tools    |
                  +----------------------+

Cloud gateway: authentication + static frontend + encrypted relay only
```

Desktop and Web use the same `desktop-react` renderer and the same host operation table.
The transport is the only difference:

- Desktop: React -> Electron IPC -> local Loom Host
- Web: React -> WSS gateway -> local Loom Host

Closing the Desktop window does not stop Loom Host. On Windows the installed app registers a
background-host login launch and keeps a tray entry so Web access remains available without
an Electron window being open. The tray provides **Open Loom**, **Open Loom Web**, and
**Quit Loom Host**.

## Security boundary

- Browser authentication uses Secure, HttpOnly, SameSite=Strict cookies.
- The local Host connects **outbound** to `wss://loom.smirel.com/api/ws/device`; no inbound
  port is opened on the user's computer.
- The Host authenticates with the user's Loom Account access token in the WSS Authorization
  header. Browser JavaScript never receives that token.
- The gateway only connects browser and Host peers belonging to the same authenticated user.
- Operation names are allow-listed by the Host's shared `desktopOperations` table.
- App Server approvals, sandboxing and permission checks are unchanged because Web invokes
  the exact same local App Server as Desktop.

## Endpoints

- `GET /api/healthz`
- `GET|POST /api/auth/*` — same-origin account facade
- `WS /api/ws/browser` — authenticated browser transport
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
