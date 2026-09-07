# Loom Runtime v2 — execution layer

This layer sits below the model/tool loop and owns managed process execution, PTY lifecycle, environment sanitization, and OS-sandbox planning.

## Managed commands

`ProcessStore` owns process identity and lifetime. A process belongs to one Loom session and records the permission mode under which it was launched.

Primary built-in tools:

- `exec` — unified foreground/background command execution
- `exec_wait` — wait for or poll a managed process
- `exec_write` — write to stdin / PTY
- `exec_resize` — resize a PTY
- `exec_interrupt` — interrupt a process
- `exec_terminate` — terminate a process tree

Legacy process tool aliases remain for compatibility, but they enter the same `ProcessStore` lifecycle.

Commands use an argv array with `shell=False`. Secret-like environment variables are removed before launch, secret-like explicit overrides are rejected, and working directories are mechanically constrained to the selected workspace.

The finalized child environment is built once before sandbox planning and is then reused for the actual spawn. This is important on Windows: MXC receives the same sanitized environment that the child process will receive, so executable/runtime read paths and environment grants cannot drift between planning and launch.

Foreground pipe commands close stdin after their optional initial input, matching one-shot execution semantics. PTY commands remain interactive. On POSIX Loom uses a real PTY; on Windows it uses ConPTY through `pywinpty`.

On POSIX, commands launch in their own process session so cancellation and termination target the process group. On Windows, Loom keeps the platform process-tree termination path. Foreground cancellation is checked while the command is running rather than only before/after `subprocess` returns.

Output has two representations:

- incremental `process_output` events for observers/UI
- a bounded final transcript returned to the model

## OS sandbox boundary

`SandboxManager` consumes the same resolved permission snapshot used by tool authorization. It does not reinterpret permission names independently.

- unrestricted filesystem access intentionally bypasses OS sandboxing
- read-only selects a read-only workspace policy
- workspace-write selects a writable workspace policy
- `LOOM_SANDBOX_POLICY=required` fails closed when the requested OS sandbox is unavailable
- `auto` uses a supported backend when available and otherwise reports an honest unsandboxed fallback
- `off` explicitly disables OS sandboxing

### Linux

Linux uses Bubblewrap when `bwrap` is available and its probe succeeds. The workspace is writable only for workspace-write mode; control-plane metadata such as `.git`, `.loom`, and `.agents` is rebound read-only when present.

### Windows

Windows can use Microsoft MXC `wxc-exec.exe` with the stable `0.8.0-alpha` configuration contract. Loom discovers it from `LOOM_WINDOWS_SANDBOX_EXECUTABLE` or PATH.

Availability is not inferred from file presence or `--version`. Loom runs MXC's read-only `--probe` and only marks the backend usable when the probe returns a recognized ProcessContainer isolation tier (`base-container`, `appcontainer-bfs`, or `appcontainer-dacl`). If the probe cannot establish a usable tier, `required` mode fails closed and `auto` reports fallback instead of claiming isolation.

The Windows policy currently requests:

- workspace read/write only in workspace-write mode
- workspace read-only in read-only mode
- only concrete, existing runtime roots required by the selected executable (plus existing explicit Python runtime roots) as additional read grants
- `.git`, `.loom`, and `.agents` read-only when present
- outbound network denied
- inbound and host-loopback network denied
- UI disabled
- clipboard access disabled
- input injection disabled
- a filtered child environment with secret-like variables excluded

`PATH` remains child-process lookup data; Loom does **not** expand every PATH entry into an MXC filesystem grant. This matters on AppContainer+DACL hosts because every explicit read root may require ACL work, and host PATH values commonly contain stale, nonexistent, or unrelated directories. Filesystem grants therefore stay minimal and are derived from the executable actually selected for the command.

The repository CI installs the official `@microsoft/mxc-sdk` package on `windows-latest`, runs `wxc-exec --probe`, and then executes real enforcement smoke tests. Those tests verify that workspace writes succeed only when allowed, writes outside the workspace are blocked, read-only workspace writes are blocked, secret-like environment values do not cross the sandbox boundary, and network connectivity is denied.

MXC is **not bundled into Loom yet**. A normal Windows installation therefore reports the Windows sandbox as unavailable until `wxc-exec.exe` is installed/provided. Some MXC fallback tiers can also require one-time privileged host preparation; Loom does not silently elevate or modify host ACLs to perform that preparation.

When a session permission mode changes, Loom terminates running managed processes from that session. This is deliberately conservative because containment selected for an already-running process cannot be retroactively replaced by a stricter profile.

## Patch runtime

`apply_patch` is the primary multi-file edit primitive. The first patch language is structured JSON so behavior is deterministic across Windows/Linux and does not require Git or an external `patch` executable.

Supported operations:

- `add(path, content)`
- `update(path, old_text, new_text)` for one exact match
- `update(path, content)` for a complete-file replacement
- `delete(path, expected_text?)`
- `move(path, move_to)`

All operations are planned against an in-memory virtual workspace first. If any path, precondition, encoding, size or exact-match check fails, no filesystem writes occur. Immediately before commit Loom checks the preimages again so changes made between planning and execution fail closed.

Writes are staged in temporary sibling files and committed with `os.replace`. If a commit-stage error occurs after an earlier path was changed, Loom performs a best-effort rollback to the validated preimages.

## Turn diff tracking

`TurnDiffTracker` records the first preimage and latest postimage of every file changed through Loom file tools. It therefore computes a net turn diff without invoking `git diff` and works in non-Git workspaces.

The existing `write_workspace_text` and `replace_workspace_text` compatibility tools also feed this tracker. `apply_patch` returns the current net diff, `get_turn_diff` exposes it explicitly, and the runtime emits `turn_diff_updated` whenever a tool changes the tracked revision.

This is intentionally a runtime-owned service rather than tool-global state. The same pattern is used for `ProcessStore`, Browser, MCP, sandbox, and other execution services.
