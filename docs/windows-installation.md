# Windows installation lifecycle

The NSIS installer hooks live in `desktop-react/build/installer.nsh`,
`processes.nsh`, `upgrade.nsh` and `upgrade-entry.nsh`. Electron-builder still
owns installation mode, registry discovery, file extraction, shortcuts and its
standard uninstall result handling.

## First installation

1. Electron-builder selects the per-user/per-machine directory and prevents a
   second installer from running concurrently.
2. Loom checks the exact application executable and processes whose executable
   paths are inside that installation's `resources\` directory. The directory
   comparison includes the trailing separator and ignores case. Setup and
   uninstall executables in the installation root are excluded.
3. With no registered previous installation, upgrade cleanup returns immediately.
4. Electron-builder extracts the payload and registers shortcuts/uninstall
   metadata. Loom registers `loom://` with a quoted executable and URL argument.
5. Interactive installation launches the app; silent installation follows
   electron-builder's `--force-run` behavior.

## Replacement and automatic update

Manual setup and electron-updater use the same installer. `--updated` skips the
initial close confirmation; it does not skip process checks. The Host updater
also tells connected desktop clients to quit before starting setup.

Loom first requests graceful window closure. If processes remain, it kills their
trees by PID, including bundled Python/browser helpers and orphan runtime
processes. Same-named applications in other installations are left running.
Paths are passed to PowerShell through environment variables, so spaces and
apostrophes cannot change the query. The process check has three outcomes:

- `0`: matching processes exist; close them and check again.
- `1`: none exist; continue.
- Any other result: abort with a process-query error, rather than displaying a
  misleading “cannot close Loom” message or silently overwriting live files.

A same-named process whose executable path cannot be inspected also produces a
query error, so an elevated app is not silently treated as absent.

Process outcomes and command errors append to `%TEMP%\loom-installer.log`.
Cancellation and a failed shutdown return exit code 2.

The upgrade hook discovers the old location from the existing installation
registry, falling back to the quoted uninstall command. It rejects missing
locations and drive/system/application-data roots. A stale registration without
an app or uninstaller does not authorize recursive cleanup.

**Upgrade always runs the uninstaller generated with the new installer**, in
NSIS's temporary plugin directory. It never executes the installed legacy
uninstaller. This is necessary because older electron-builder process checks
matched every process under the installation directory and could count setup
itself as the app. Merely changing the new installer's check does not fix that
legacy upgrade path.

The new uninstaller receives the old directory via `_?=`, the original install
scope, and the existing shortcut/data flags. Upgrades retain user data by
default; `--delete-app-data` remains an explicit request. Electron-builder's
upgrade uninstaller moves old files into its temporary directory before removal
and restores moved files if it cannot move a locked file. Its nonzero result
stops the parent installer; cleanup is not retried with the legacy executable.
This file-move rollback does not provide rollback of the entire installation if
subsequent payload extraction fails.

## Normal uninstall

The installed uninstaller uses the same process check and shutdown logic. It
removes application files, shortcuts and installation registry entries. The
`loom://` entry is removed only if its command still points at this installation.
User data is retained unless explicitly requested otherwise. NSIS normally
relaunches uninstall from a temporary copy, so the original process exiting does
not mean filesystem cleanup has finished.

## Verification and publishing

Run `npm run test:installer` on Windows after electron-builder has downloaded its
NSIS compiler. It builds a small real NSIS fixture under a unique app ID, package
name, protocol and updater cache, then checks:

- A failed process query aborts and writes a diagnostic log.
- First install works with a space/apostrophe-containing path and no running app.
- An intentionally broken old uninstaller is bypassed during replacement.
- Obsolete files are removed and user data survives replacement/uninstall.
- Manual replacement closes an app and its bundled child process.
- Automatic-update replacement closes an orphan bundled runtime.
- An unrelated same-named application remains running throughout.
- Normal uninstall finishes and removes its protocol registration.

The smoke test uses per-user installation and never touches production Loom
registry keys, protocol or updater cache. Successful test artifacts are removed;
failed fixture directories are retained for diagnosis. Per-machine elevation and
interactive permission-denial dialogs still require a separate manual check.

`package:win` runs the existing packaged Host/renderer checks plus this lifecycle
smoke test. `package:win:publish` uses that verified packaging path before
publishing, rather than bypassing the checks.
