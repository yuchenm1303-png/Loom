# Typed execution-action alignment

This stacked branch starts separating **what the user is approving** from the generic tool-definition binding.

Current Codex models approval as a structured `ApprovalAction`: an exec action carries the command, cwd, TTY state, sandbox permissions and environment identity rather than relying on a tool name plus free-form reason. Its approval/cache key is derived from execution semantics rather than the per-request call id. Loom is moving toward the same observable invariant without copying Codex's Rust type shape.

## `ExecActionIdentity`

`ExecActionIdentity` is a frozen, secret-minimized description of one model-originated `exec` action. It currently captures:

- the protocol call id plus argv;
- requested and resolved workspace cwd;
- wait/timeout behavior;
- PTY state and effective terminal dimensions;
- a process-local identity for stdin instead of copying stdin content;
- explicit environment variable names plus a process-local identity for their values;
- the identity of the final child environment after `ShellEnvironmentPolicy` applies the explicit overrides.

The action parser reuses Loom's existing argv, timeout and terminal-size validators. Workspace cwd resolution is fail-closed, and explicit environment values must remain strings just as the real exec handler requires.

`execution_action_for(step, call)` is the generic entry point for downstream runtime code. It currently returns an `ExecActionIdentity` only for `exec`; later action types can be added without teaching Core call sites about each tool's argument shape.

## Instance identity versus execution identity

The action object keeps request data that is useful for tracing, but its semantic binding deliberately canonicalizes values that do not change execution:

- `call_id` identifies the protocol request and is excluded from the action digest;
- requested cwd spelling is retained for the instance payload, while the semantic key uses the resolved workspace path (`.` and `./` are equivalent);
- rows/cols are still validated for every request, but they are excluded from effective action state when PTY is disabled because the pipe backend does not consume them.

This lets an approval key represent what will actually execute instead of hashing incidental JSON spelling.

## Secret handling

The typed action is not another copy of command secrets. Its binding payload deliberately omits raw stdin and environment values. Those values are represented by process-local HMAC identities. The already-existing user-visible `ToolCall`/approval payload remains the place where requested arguments live.

Moving the process-local environment identity into this module also gives Loom one source of truth for exec execution identity; `execution_binding.py` no longer owns a separate HMAC implementation.

## `action_binding_digest`

The branch now defines the target call-specific approval-key shape without switching Core persistence yet:

- `binding_digest(step, tool, platform)` remains the tool + frozen-step identity;
- `action_binding_digest(step, tool, call, platform)` composes that base binding with a typed action digest when one exists;
- `exec` therefore adds canonical argv/cwd/PTY/stdin/environment semantics;
- tools that do not yet have a typed action return the existing binding unchanged;
- a call/tool-name mismatch fails closed.

This gives Core one migration target instead of another side-channel state store. The eventual atomic integration is to use `action_binding_digest` both when `pending_bindings` are created and at every resume/pending validation site.

## Deliberate next boundary

This branch does **not** yet switch the durable `pending_bindings` call sites. That change must update binding creation and every validation path together; changing only one side would intentionally make pending approvals fail closed but would not be a usable migration.

The Core integration should therefore be one small, atomic patch rather than adding a parallel pending-action map in `SandboxAgentRuntime`.
