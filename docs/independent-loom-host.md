# Independent Loom Host

Loom Host and Loom Desktop now run in separate OS processes. They ship in one
installer and use the same executable, with different entry modes:

- `Loom.exe --loom-host`: background Host, tray, local discovery, web relay,
  account session, model manager, Agent Runtime, and Computer Use HUD.
- `Loom.exe`: desktop interface; starts or connects to the Host.
- `loom://...`: starts the Host without requiring a desktop window.
- The legacy `--loom-background-host` launch argument remains supported.

On Windows the installed Host registers itself for login startup. After the
initial installation/login or browser pairing, users can open Loom Web directly.
Closing or quitting Desktop leaves Host and active tasks running. The Host tray
opens Desktop or Web; **Quit Loom Host** explicitly stops the local runtime.
The tray loads the bundled Loom icon, including during development, rather than
relying on the generic Electron executable icon.

Both entry modes acquire independent single-instance locks. The existing
`userData` directory remains the Host account/device store; the desktop Chromium
profile moves into its `desktop-ui` subdirectory. Existing browser preferences
are copied on first desktop startup. Conversation/runtime data remains in the
existing runtime home (`LOOM_HOME` or `~/.loom`). No account or provider secrets
are copied to the UI profile.

Desktop backend IPC is forwarded over a local named pipe (Windows) or Unix
socket. Only registered operations are accepted. A fresh per-Host credential
authenticates each client; it is stored outside Git in `userData/host-ipc`, with
Windows ACLs restricted to the current user and SYSTEM, or mode 0700/0600 on
Unix. Actual account/provider secrets retain their existing encrypted storage.
Messages are bounded, support binary attachments, and have request timeouts.
Disconnecting a desktop client never stops the shared runtime. Reopening the
desktop attaches to the running Host and reloads persisted thread state.

Host owns software updates. Update status is forwarded to Desktop. Before an
installer starts, Host asks desktop clients to quit, then stops through its
normal shutdown hooks; the updater restarts the installed application.

## Validation

Run from `desktop-react`:

```powershell
npm run test:electron
npm run test:host-process
npm run typecheck
npx vite build
```

The process smoke check runs actual Electron Host/client processes with isolated
data, no real account, and a temporary discovery port. It verifies Host starts
without Desktop, survives client exit, and accepts another client. The transport
tests cover notification forwarding, binary arguments, unknown operations,
wrong credentials, pending-request rejection, and reconnection.

For isolated development checks, `LOOM_HOST_DATA_DIR` overrides the Host account
store and `LOOM_HOST_DISCOVERY_PORT` overrides discovery port 39223. Set the same
values in both processes. These overrides are not needed in normal installations.

Packaged acceptance still needs a new Windows build: confirm the Loom tray icon,
login startup, web pairing, desktop exit during a running web task, reopening the
same thread, and an installer update while both processes are running.
