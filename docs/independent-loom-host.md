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
opens Desktop or Web; **Quit Loom** in the Host tray explicitly stops the local runtime.
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
normal shutdown hooks; a restart marker restores the Host role before acquiring
its single-instance lock, so an update does not accidentally launch Desktop.

Process lifecycle lives in `electron/hostProcess.ts`. Agent distribution lives
in `electron/hostRuntime.ts` and `electron/hostRuntimeUpdater.ts`; these modules
are complementary parts of one architecture. Only Host activates runtime updates,
and both clients resolve runtime assets from the original Host data directory.
Runtime activation waits for idle work, reloads the shared App Server and browser
assets, and restores the prior runtime pointer if activation fails.

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

## Web entry

The web portal distinguishes starting an installed Host from installing Loom on
a new computer. It offers `loom://host/start` as a user-initiated launch action;
it does not automatically launch an external application. Once a signed-in Host
is connected, the portal stays visible until the user clicks Open workspace. Connection loss returns to the
Host entry and retries in the background. Failure to discover localhost is not
proof that Loom is uninstalled: the same entry offers both start and install.

Pairing requests have a bounded lifetime and are canceled when the account or
Host changes. Polling new Host snapshots does not cancel an in-flight pairing
request. Connection responses from a previous account cannot enter a workspace
after sign-out or account switching. Detailed connection errors are available
in an expandable section while the main copy explains the next action.
