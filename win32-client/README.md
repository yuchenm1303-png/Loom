# Loom Windows x86 remote companion

This is the **32-bit remote client**, not a 32-bit local Loom Host. It
renders the production Loom Web React UI at https://loom.smirel.com/ inside
an x86 Microsoft Edge WebView2 process. The desktop x64 installation and
the existing electron-updater / Python runtime are unchanged.

## Why a separate x86 shell?

Electron 44 and later no longer ship Windows ia32 binaries. The current
Loom x64 app also ships x64-only Windows MXC executables, an x64 frozen
Python Host and an x64 Playwright Chromium. Changing electron-builder's
architecture flag would not create a working local x86 agent.

WebView2 Evergreen is maintained independently of Electron and supports
Windows x86. The web page is built from the same `desktop-react` React
sources as the x64 desktop. The x86 companion uses the existing *remote*
transport to an explicitly selected online **x64 Loom Host**. It cannot
run tools on the 32-bit computer itself.

## Supported and unsupported

- Supported: Loom account login, viewing the portal, selecting an online
  remote Host, messaging/conversations/approvals and managing tools **on
  that remote Host** through the existing authenticated gateway.
- Unsupported: starting a local Host, accessing this PC's files, local
  Computer Use or Browser Use, local tool execution and offline chat.
- Needs Windows 10/11 **32-bit**, Microsoft Edge WebView2 Evergreen Runtime
  and an internet connection. The x86 executable also runs on x64 Windows
  but must not replace the full x64 Loom desktop.
- The WebView2 loader must be packaged for x86. Native executable is
  validated as PE machine `0x014c` in CI.
- This companion intentionally has a **distinct app identity, install
  directory and cookie profile**. It has no native bridge or local agent
  executable, and does not use the x64 auto-update feed.
- The host selection / remote access authorization remains enforced by
  Loom Web. An offline Host remains unavailable; the app must never
  silently connect to another user's/local machine.
- The native shell launches `https://loom.smirel.com/?loom_client=win32-remote`.
  The live Web renderer recognizes this marker, suppresses local Host
  discovery and shows a chooser before connecting. Browser-based Loom Web
  without this marker retains its original local-first behavior.
- The **production web renderer must be deployed with the corresponding
  `webBridge.ts`, `WebAppGate.tsx`, `WebPortal.tsx` and
  `RemoteDevicesPanel.tsx` changes**. A new x86 installer alone cannot
  activate the remote-only mode against an older deployed webpage.

## Expected first-run flow

1. Launch Loom Remote (32-bit). Sign in on the normal Loom account page.
2. Choose **Choose remote computer / 选择远程电脑** from the home screen.
3. Pick an online Loom Host belonging to that account. Hosts from another
   account are not accessible, and no local Host is started.
4. Enter the workspace. Files and tools refer to the **selected remote PC**.
5. Switching remote computers is always explicit. Restarting the shell
   requires choosing the target again; it never automatically picks another
   computer based on account history.

## Build

On Windows with .NET 8 SDK:

```powershell
dotnet publish win32-client/LoomWin32Remote.csproj -c Release -r win-x86 --self-contained true -o win32-client/bin/publish
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" win32-client/installer.iss
```

The GitHub Actions `win32-remote-client.yml` workflow validates the
shared Web frontend remote-only flow, builds the
`Loom-Remote-Setup-x86.exe` installer and verifies PE architecture before
uploading its artifact. Publication to an existing GitHub Release is an
explicit manual workflow action only, not automatic.

## Manual validation needed before public release

On a physical Windows x86 host, check install/uninstall, WebView2 missing
runtime flow, account login (password + Google/GitHub OAuth), selection
of a remote x64 Host, conversation streaming and sign-out. CI can validate
the published PE architecture, not physical hardware support.
