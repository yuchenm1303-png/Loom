# Loom on macOS — experimental Apple Silicon build

Status: **developer preview**. This branch does not publish a signed or notarized
Mac installer and must not replace the Windows release until native smoke tests pass.

## Scope

- The same React/Electron UI, Python Agent runtime, browser tools, workspace and
  local Host/desktop IPC are reused.
- The frozen Host runtime, Chromium payload, architecture-tagged runtime manifest
  and unsigned arm64 DMG have a dedicated macOS build gate.
- On macOS the Computer Use tool uses the **primary display** screenshot and
  pyautogui coordinates after explicit macOS consent. Unlike the Windows backend,
  this does not provide accessibility-tree control discovery, individual window
  targeting, secondary displays, or Unicode text injection.
- macOS OS-level sandboxing has **not** been implemented. The existing Loom
  approval policy still applies, but it is not a substitute for an OS sandbox.
- Automatic desktop updates remain Windows-only until Mac's signing,
  notarization, update feed, and rollback path have been independently verified.

## Build on an Apple Silicon Mac

Requires macOS 13+, Python 3.11, Node 22, Xcode Command Line Tools and adequate
disk space for Chromium and PyInstaller. From the repository root:

```sh
cd desktop-react
npm ci
npm run package:mac:arm64
```

The command builds the UI, bundles a private macOS arm64 Python Host with
Playwright Chromium, validates a fresh no-credential runtime startup and packs
`release/Loom-<version>-mac-arm64.dmg`. The output is **unsigned**. For
development, macOS may require explicitly approving the application in
System Settings > Privacy & Security after attempting to open it.

For an automated build, use the **macOS Desktop (experimental)** GitHub Actions
workflow on the feature branch. It checks architecture, builds the app, tests the
frozen Python Host and saves the candidate DMG as a short-lived artifact. The
workflow does not publish or promote it to a release.

## Computer Use permission setup

The first screen capture requires **Screen & System Audio Recording** (called
**Screen Recording** on older macOS releases). Pointer and keyboard control
also require **Accessibility**. Grant both in System Settings > Privacy &
Security for Loom and relaunch the app after permission changes. Loom checks
consent and refuses to inject input if either requirement is missing.

Visual-only macOS observations expose no semantic control IDs. When an action
requires one of these, Loom fails explicitly rather than silently redirecting
the click to the wrong target. Computer Use tasks involving multiple monitors
or Unicode typing are not complete yet. Browser-native tools should be
preferred for web form input.

## Promotion checklist

1. Get green macOS arm64 CI on the feature branch.
2. Install the DMG on an actual Apple Silicon Mac, verify Host RPC, model switch,
   local workspace file operations, browser capture and long-running sessions.
3. Verify real Screen Recording and Accessibility TCC prompts plus input control.
4. Add verified macOS command process isolation or clearly expose the missing
   OS sandbox before the stable release.
5. Configure Apple Developer ID signing/notarization and a Mac-specific updater
   feed, then test upgrade, rollback and background Host recovery.
6. Keep the Windows installer and Host runtime regression suite green.

Neither CI compilation nor a DMG existing proves real macOS UI automation.
