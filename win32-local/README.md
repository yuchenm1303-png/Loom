# Loom Windows 32-bit **local Host** preview

This is the requested native **local** Loom Desktop + Agent build; it is not
the remote-only WebView2 companion under `win32-client/`.

- Uses **the same main-process and renderer sources** as the full x64 Loom
  desktop, but compiles them with Electron 43 Windows ia32.
- Bundles **32-bit frozen Python Agent** (from the same Loom source) and
  implements the original Desktop -> Host -> App Server IPC architecture.
- Uses an independent x86 app identity, data path, installer and x86-only
  release channel; prevents the x86 app from installing any x64 update.
- Does **not** bundle x64 Microsoft MXC sandbox executables or x64
  Chromium. It must never attempt to execute those files on a 32-bit OS.
- The **Loom embedded browser** is part of the native x86 Electron renderer.
  Separate Playwright/browser-use automation uses a checksum-verified upstream
  **32-bit Node.js** driver. In "system browser" mode it prefers an installed
  Microsoft Edge or Chrome on the **same PC**. No browser is secretly fetched
  from a 64-bit release. Browser automation still needs physical-device QA.
- Native Windows Computer Use imports are smoke-tested within the packaged
  Python x86 process; image capture, pointer input and UIA actions need
  end-to-end testing on a real interactive Windows 10 x86 desktop.
- `pywinpty` currently has no compatible Python x86 wheel. In that specific
  case `pty=true` degrades to a **pipe-backed** interactive process on 32-bit
  Windows only. Commands, stdin, stdout and stderr are usable; terminal
  resize / full-screen TUIs are not. The backend accurately reports
  `pty=false` and `windows-x86-pipe-terminal`. x64 behavior is unchanged.
- The Microsoft MXC **OS sandbox** is still unavailable on x86. Sandbox
  `required` must fail closed. Do not claim sandbox parity or turn off
  permission checks to conceal the missing backend; security-sensitive shell
  execution needs explicit review before a wide release.
- Intended for **Windows 10 x86**. Windows 11 has no 32-bit editions, and
  Electron 43 does not support Windows 7/8.1.
- This remains an **experimental preview** until account login, chat,
  remote Web-to-local Host pairing, file tools, real browser tasks and native
  Computer Use input/screenshot all pass on a physical Windows 10 x86 system.
  The CI host is a **64-bit Windows runner executing x86 binaries**: this is
  a meaningful runtime smoke but not physical x86-OS validation.
- x86 auto-updating is intentionally **disabled**, rather than risking
  installing an x64 update. Install future verified x86 packages manually
  until a separately secured x86 release/update feed is ready.
- Electron 43's upstream security maintenance ends in January 2027, so a
  supported longer-term 32-bit shell plan is required before public release.

CI in `.github/workflows/windows-x86-local.yml` builds and executes the
actual x86 Python runtime and native Electron package on a Windows runner.
It produces **Loom-Setup-x86-<version>.exe** only when its gates pass.
The existing production x64 workflow is unchanged.
