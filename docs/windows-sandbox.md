# Windows sandbox (Microsoft MXC)

Workspace mode runs `exec` commands inside a Microsoft MXC process container
(`wxc-exec.exe`, `@microsoft/mxc-sdk` 0.8.0). This page records what the sandbox
grants and blocks on Windows, what was measured, the one-time host preparation it
needs, and how a command the sandbox stopped is handled.

Measured 2026-10-23/24 on Windows 11 23H2 (build 22631). That build is below MXC's
documented minimum (24H2, build 26100) for its `BaseContainer` tier, so MXC runs in
its fallback tier, `appcontainer-dacl`. GitHub's `windows-latest` runner reports the
same tier. Anything below that depends on the tier is marked.

## What the sandbox is

`SandboxManager._windows_mxc_argv` builds one inline JSON config per command:

| Area | Setting |
| --- | --- |
| Filesystem | workspace read/write (`.git`, `.loom`, `.agents` read-only); the program's folder, `SYSTEMROOT`, `PYTHONPATH` entries read-only; nothing else |
| Network | egress, ingress and host loopback denied |
| Environment | only a fixed allow-list plus `LOOM_EXEC_*`; names containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD` are dropped |
| Capability | `loomSandboxTools` (see host preparation) |
| UI | Win32k reachable; clipboard `none`, injection `false`, no desktop control, IME off |

Read-only mode drops the workspace write grant. Full access never wraps the command.

## UI access: what was measured

Blocking every Win32k call (`ui.disable: true`, the previous setting) makes any program
that links `user32` die at start with `STATUS_DLL_INIT_FAILED` (`0xC0000142`). Run
through the real `wxc-exec`, with Win32k blocked ("lock") and allowed ("open"):

| Program | lock | open |
| --- | --- | --- |
| cmd, findstr, xcopy, icacls, python | runs | runs |
| node, PowerShell, git, java, curl, robocopy, where | `0xC0000142` | starts |
| npm (node + `npm-cli.js`) | `0xC0000142` | starts; needs the system-drive step below |
| git | `0xC0000142` | starts; needs the NUL step below |
| tar, hostname, whoami | `0xC0000142` | `0xC0000142` (not a Win32k problem) |

A program allow-list for Win32k therefore cannot work: `node` alone is enough to run
arbitrary code. Win32k stays reachable for every command, and the exposure was measured
with a driver outside the sandbox and a probe inside it (a control run outside the
sandbox proves the harness sees what it should):

| Probe from inside the sandbox | Result |
| --- | --- |
| Open the clipboard | blocked (`ERROR_ACCESS_DENIED`) |
| Enumerate other processes' windows | none returned |
| `PostMessage` / `SendMessageTimeout` to a window of the host app | blocked (`ERROR_INVALID_WINDOW_HANDLE`), the window received 0 messages |
| `SendInput` (F24, four events) | returns 1 but a low-level keyboard hook outside saw **0** events (control: 4) |
| Low-level keyboard hook / `GetAsyncKeyState` while keys are pressed outside | saw nothing |
| `SetCursorPos`, `SystemParametersInfo` (set the beep flag to its current value) | blocked (`ERROR_ACCESS_DENIED`) |
| **Read the screen** (`GetDC(NULL)` + `BitBlt` + `GetDIBits`) | **works**: a 300x300 capture was byte-identical (same SHA-256) to the same capture taken outside the sandbox |

So with Win32k reachable a sandboxed command can capture what is on the screen. It cannot
type, click, read the clipboard, or touch other applications' windows. The four
`processContainer.ui.isolation` values (`desktop`, `handles`, `atoms`, `container`) do not
change this in the fallback tier, and a private desktop is not possible: `wxc-exec` pins
the child to `winsta0\default` (the string is in the binary), so launching `wxc-exec`
itself on another desktop does not move the sandboxed process (verified: the child
reported desktop `Default` while a direct child reported the private one and saw only a
black screen).

Consequences and choices:

* The screen is not protected by this sandbox. Computer Use's explicit, visible
  screenshot flow is not what protects it here.
* `LOOM_WINDOWS_SANDBOX_UI=locked` (or `SandboxManager(windows_ui_access=False)`) restores
  the Win32k lockdown. The tools above then fail at start and go through the denial and
  approval flow below.
* `SandboxSnapshot.display_access` and `get_sandbox_status` report whether the screen is
  reachable.
* Untested: on Windows 11 24H2+ MXC's `BaseContainer` tier documents `ui.isolation:
  desktop`. If that tier isolates the desktop, it would close the gap. Verify before
  relying on it.

## Host preparation (one time, administrator)

Two things are out of an AppContainer's reach until an administrator opens them, and
protected tool folders need a grant (`appcontainer-dacl` tier):

1. **The NUL device.** Its security descriptor names no AppContainer SID and the kernel
   resets it at every boot. `git` opens `/dev/null` unconditionally and fails with
   `fatal: could not open '/dev/null'`.
2. **The system-drive root's metadata.** `node`'s `realpath` fails with `EPERM: lstat
   'C:\'`, which is what stops `npm`.
3. **Protected tool folders**, e.g. `C:\Program Files\nodejs` (administrators own it and the
   user cannot change its ACL, so MXC reports `DACL fallback requires write-DAC
   permission`). Declaring such a folder fails the command; the folder has to be readable
   *without* being declared.

`scripts/windows-sandbox-prep.ps1` does all three:

```powershell
# What would it change? Needs no administrator rights, changes nothing.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows-sandbox-prep.ps1 -DryRun
# Apply (asks for elevation, UAC)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows-sandbox-prep.ps1
# Report the current state, no administrator rights needed
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows-sandbox-prep.ps1 -Status
# Remove everything it added
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows-sandbox-prep.ps1 -Undo
```

What it changes:

* copies Microsoft's `wxc-host-prep.exe` to `%ProgramFiles%\Loom\SandboxPrep` after
  checking its Authenticode signature (Microsoft Corporation), and runs it with
  `prepare-system-drive` and `prepare-null-device`;
* registers the task `\Loom\Sandbox NUL device`, which runs that copy as SYSTEM at startup
  to reapply the NUL descriptor;
* grants read and execute on discovered tool folders (`git`, `node`, `npm`, `go`, `java`,
  `dotnet`, `gh`, ...) to the SID of Loom's own capability `loomSandboxTools` only
  (`S-1-15-3-1024-3969206430-...`), not to `ALL APPLICATION PACKAGES`. Folders already
  readable, system folders, and folders under the user profile are skipped.

Writes outside the workspace, reads of the user profile (`~/.ssh`) and network access are
unchanged by any of this. Verified on a user-owned copy of the node folder with the same
ACL shape (protected, with and without the capability ACE): declaring the folder fails with
the write-DAC error; not declaring it, node starts but cannot read its own files (`EPERM`);
with the capability ACE and the folder not declared, node starts and reads them.

Loom reads these states without elevation (`windows_sandbox_prep.py`). When the tier is
`appcontainer-dacl` and something is missing, the sandbox reason says what and prints the
command, and so does every sandbox denial that the missing step explains.

`SandboxManager` also drops a declared read-only path from the MXC config when the sandbox
can already read it through the capability or `ALL APPLICATION PACKAGES`; MXC would
otherwise try, and fail, to rewrite a DACL it cannot edit.

## Sandbox denials

`sandbox_denial.classify_exec_sandbox_denial` turns a failed `exec` into a typed result
(`failure_kind: sandbox`, `sandbox_failure: denied`, plus `sandbox_denial`). On Windows MXC
it recognizes, from specific signals only:

| kind | signal | startup |
| --- | --- | --- |
| `win32k` | MXC `backend_error` JSON naming `STATUS_DLL_INIT_FAILED` / Win32k | yes |
| `dll_init` | exit `0xC0000142` | yes |
| `dacl` | `DACL fallback requires write-DAC permission on '<path>'` | yes |
| `null_device` | `could not open '/dev/null'` / `\\.\nul` with an access error | yes |
| `system_drive` | `EPERM`/`EACCES` on `lstat 'C:\'` | yes |
| `launch` | `STATUS_ACCESS_DENIED`, or a `backend_error` that is an access failure | yes |
| `access` | an access-denied message (English, Chinese, `EPERM`/`EACCES`) | no |

A `backend_error` that is not an access failure (for example the program was not found)
is not a sandbox denial. Everything else keeps Codex's keyword heuristic.

What happens next:

* `startup` denials mean the command never ran, so under `on-request` (workspace mode)
  Loom asks to run that exact call once outside the sandbox. The card shows the reason
  ("git.EXE could not start inside the Windows sandbox ...") and the one-time fix command
  instead of a bare exit code. Under `unless-trusted` (approval mode) and `granular` the
  existing Codex rule applies: a call the reviewer already approved is retried outside the
  sandbox without a second prompt, so in approval mode a command whose sandbox cannot start
  runs unsandboxed after the one approval it was given.
* A denial that is not `startup` (the command ran and was refused something) is returned to
  the model with the reason and, under `on-request`, how to ask for escalation
  (`sandbox_permissions: require_escalated`). Under `never` it is told escalation is not
  available; under `required` it cannot leave the sandbox.
* A second denial is returned as a failure; there is no third attempt.

## Scoped approvals

An approval is no longer only yes or no. The request lists what it honors in
`availableDecisions`:

| Decision | Effect |
| --- | --- |
| `accept` | this call only |
| `acceptForSession` | also remember, until the session ends: a command prefix for `exec` (the exact command when no safe prefix exists), the tool name for any other tool |
| `acceptWithPrefixRule` | also save the command prefix for this project in `<home>/approval-rules.json` |
| `decline` | refuse; an optional `feedback` (at most 2000 characters, decline only) reaches the model as `Tool call denied by the user. The user explained: ...` |

The prefix is derived by the runtime from the waiting call, never taken from the client:
the program plus its leading subcommand words (`git status --short` -> `git status`,
`npm run build` -> `npm run build`). Shells, interpreters and launchers (`git` alone,
`python`, `node`, `cmd /c`, `powershell`, `bash -c`, `npm run`, `env`, `rm`, ... Codex's
banned list plus Windows launchers) never become a prefix, and a script with shell syntax
can only be remembered as an exact command.

A rule keeps the sandbox mode it was granted in. Approving an escalation ("run outside the
sandbox") remembers `unsandboxed`: the next matching call skips the sandbox that could not
start it, with no prompt. Approving a normal call remembers `policy`: it covers matching
calls the sandbox would run, and not the AUTO fallback that runs with no sandbox. An
`unsandboxed` rule is ignored under approval policy `never`, under `granular` without
sandbox approval, and under `SandboxPolicy.REQUIRED`. A call with explicit `env` overrides
matches no rule (`PATH` could swap the program), and scoped `additional_permissions` are
always reviewed. A bare program name matches by name only; a program given as a path
matches only that path.

The durable request identity checks of `approval/respond` (thread, turn, request, call) are
unchanged, and a decision the request did not offer is rejected while the request stays
open. `approvalRules/list` and `approvalRules/remove` read and delete saved rules.

## Not done / unexplained

* Screen reads are possible with Win32k reachable (above).
* `where.exe` starts but cannot enumerate `System32`, so PATH lookups of system programs
  report nothing; `tar`, `hostname`, `whoami` fail with `0xC0000142` even with Win32k
  open; `dotnet` exits `0x80008091`, `gh` cannot read its config in the user profile, `go`
  cannot find `GOROOT` there. None of these were investigated.
* The effect of `prepare-system-drive` and `prepare-null-device` on `git` and `npm` can only be
  confirmed after an administrator has run the script.
