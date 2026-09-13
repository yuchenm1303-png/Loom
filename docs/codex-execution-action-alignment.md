# Typed execution-action alignment

This stacked branch starts separating **what the user is approving** from the generic tool-definition binding.

Current Codex models approval as a structured `ApprovalAction`: an exec action carries the command, cwd, TTY state, sandbox permissions and environment identity rather than relying on a tool name plus free-form reason. Loom is moving toward the same observable invariant without copying Codex's Rust type shape.

## First slice: `ExecActionIdentity`

`ExecActionIdentity` is a frozen, secret-minimized description of one model-originated `exec` action. It currently captures:

- call id and argv;
- requested and resolved workspace cwd;
- wait/timeout behavior;
- PTY dimensions;
- a process-local identity for stdin instead of copying stdin content;
- explicit environment variable names plus a process-local identity for their values;
- the identity of the final child environment after `ShellEnvironmentPolicy` applies the explicit overrides.

The action parser reuses Loom's existing argv, timeout and terminal-size validators. Workspace cwd resolution is fail-closed, and explicit environment values must remain strings just as the real exec handler requires.

## Secret handling

The typed action is not another copy of command secrets. Its binding payload deliberately omits raw stdin and environment values. Those values are represented by process-local HMAC identities. The already-existing user-visible `ToolCall`/approval payload remains the place where requested arguments live.

Moving the process-local environment identity into this module also gives Loom one source of truth for exec execution identity; `execution_binding.py` no longer owns a separate HMAC implementation.

## Deliberate next boundary

This branch does **not** yet replace the durable `pending_bindings` format. The next integration step is to let approval binding accept a call-specific action identity so command/cwd/PTY/environment semantics become part of one structured approval key end to end.

That integration should be done at the Core binding call sites, not by adding another parallel pending-action map in `SandboxAgentRuntime`.
