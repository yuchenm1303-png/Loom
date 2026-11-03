# Loom for Windows 32-bit

The 32-bit build is the same Loom desktop, Agent and local Host as x64, built
for ia32. There is one build recipe for both architectures; the x86 path only
differs where an x64 payload has no 32-bit counterpart.

## How it is built

- `npm run build:runtime:win:x86` runs `scripts/build-windows-runtime.mjs
  --arch=ia32` with a 32-bit Python. It installs the same requirements as x64,
  read from `pyproject.toml`, except `pywinpty` (no win32 wheel), and pins
  `cryptography==48.0.1` (last series with a win32 wheel). It writes the same
  GitHub OAuth release config and runs the same self-test, first-run-test and
  packaged App Server gate as x64.
- `prepare-playwright-x86.mjs` swaps Playwright's x64 Node driver for the
  checksum-verified official win-x86 `node.exe`.
- `build/win32-local.config.cjs` packages with Electron 43 (the last line that
  ships Windows ia32) under its own app id, data folder (`%APPDATA%\Loom-x86`)
  and installer. `build/installer-x86.nsh` registers `loom://` without taking
  it from an installed x64 Loom.
- `verify-local-host.ps1` checks every bundled `.exe/.dll/.pyd/.node` is x86,
  starts the Playwright driver and installed Edge, the pipe terminal, the
  App Server, the background Host and a desktop window attaching to it.

## When it runs

- **CI** (`windows-x86-local.yml`): every push to main and every PR that
  touches the app, desktop or Python code. A change that breaks x86 shows up
  on that commit.
- **Release** (`windows-x86-local-release.yml`): after each successful x64
  release, it builds that release's tag and publishes `win32-vX.Y.Z-r1` with
  one asset, `Loom-Setup-x86.exe`, as a **prerelease**, so `/releases/latest`,
  `latest.yml` and the x64 updater never see it. Running it by hand re-issues
  the current main as the next revision (`-r2`, ...) for an x86-only fix.
- The web home links the newest `win32-v*` prerelease.

## What differs on 32-bit

- **No OS sandbox.** Microsoft MXC ships x64 only. Commands whose policy
  requires the sandbox are refused; the rest go through the normal approvals.
- **Pipe terminal instead of ConPTY.** Commands, stdin and output work;
  full-screen TUIs and resize do not. The backend reports `pty=false`.
- **No bundled Chromium.** Browser tasks drive the installed Edge or Chrome;
  the embedded Loom browser works as on x64.
- **No auto-update.** Users install the next 32-bit build from Releases.
- Windows 10 32-bit only (Windows 11 has no 32-bit edition; Electron 43 does
  not run on Windows 7/8.1).
- CI runs the x86 binaries on 64-bit Windows; nothing has been run on a
  physical 32-bit Windows install.

## End of life

Electron 43's security support ends in January 2027 and no later Electron
ships ia32. From 2027-02-01 the release workflow stops publishing 32-bit
builds; CI keeps building them so the decision can be revisited.
