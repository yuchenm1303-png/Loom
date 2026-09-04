# Loom Runtime v2 — execution layer

This layer sits below the model/tool loop and above future OS sandbox integrations.

## Managed commands

`ProcessStore` owns process identity and lifetime. A process belongs to one Loom session and records the permission mode under which it was launched.

Built-in tools:

- `run_workspace_command` — foreground one-shot execution
- `start_workspace_command` — start a process and return a `process_id`
- `poll_workspace_process` — drain new stdout/stderr and inspect status
- `list_workspace_processes` — list processes owned by the current session
- `write_workspace_process` — write to stdin
- `interrupt_workspace_process` — send an interrupt
- `terminate_workspace_process` — terminate the process tree

Commands continue to use an argv array with `shell=False`. Secret-like environment variables are removed before launch. Working directories are resolved mechanically inside the selected workspace.

Foreground commands close stdin after their optional initial input, matching one-shot execution semantics. Background commands keep stdin open for later interaction.

On POSIX, commands launch in their own process session so cancellation and termination target the process group. On Windows, Loom launches a new process group and uses the platform process-tree termination path. Foreground cancellation is checked while the command is running rather than only before/after `subprocess` returns.

Output has two representations:

- incremental `process_output` events for observers/UI
- a bounded final transcript returned to the model

Runtime v2 still does **not** claim an OS-level filesystem/network sandbox. Permission profiles and approvals remain the policy layer; a future sandbox layer should enforce the same resolved StepContext permissions at the OS boundary.

When a session permission mode changes, Loom terminates running managed processes from that session. This is deliberately conservative: without an OS sandbox Loom cannot retroactively apply a stricter permission profile to a process that was launched under an older profile.

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

This is intentionally a runtime-owned service rather than tool-global state. The same pattern is used for `ProcessStore`, and is the extension point for future Browser, MCP, sandbox and other execution services.
