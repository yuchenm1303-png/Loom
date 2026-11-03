# Loom Windows 32-bit **local Host** preview

This is the requested native **local** Loom Desktop + Agent build; it is not
the remote-only WebView2 companion under `win32-client/`.

- Uses **the same main-process and renderer sources** as the full x64 Loom
  desktop, but compiles them with Electron 43 Windows ia32.
- Bundles **32-bit frozen Python Agent** (from the same Loom source) and
  implements the original Desktop -> Host -> App Server IPC architecture.
- Uses an independent x86 app identity, data path, installer and x86-only
  release channel; prevents the x86 app from installing any x64 update.
- Does not bundle x64 Microsoft MXC sandbox tools or x64 Chromium. Therefore
  **Browser Use and sandbox-assisted execution remain unsupported** and must
  not be labeled as enabled. Terminal PTY uses pywinpty, which does not
  currently build in the Windows x86 toolchain.
- This remains an **experimental preview** until packaged local Host startup,
  auth, chat, file tool and computer-use operation smoke tests pass on a real
  Windows 32-bit machine. An x64 CI runner executing x86 binaries is not
  physical x86-OS validation.
- Electron 43's upstream security maintenance ends in January 2027, so a
  supported longer-term 32-bit shell plan is required before public release.

CI in `.github/workflows/windows-x86-local.yml` builds and executes the
actual x86 Python runtime and native Electron package on a Windows runner.
It produces **Loom-Setup-x86-<version>.exe** only when its gates pass.
The existing production x64 workflow is unchanged.
