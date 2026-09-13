# Typed execution-action alignment

This stacked branch separates **what the user is approving** from the generic tool-definition binding.

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

## Call-specific binding

`action_binding_digest(step, tool, call, platform)` now composes the existing frozen Step/tool binding with a typed action digest when one exists:

- `exec` adds canonical argv/cwd/PTY/stdin/environment semantics;
- tools that do not yet have a typed action return the existing `binding_digest()` unchanged;
- a call/tool-name mismatch fails closed;
- malformed exec calls temporarily keep the generic binding so action construction does not preempt the normal tool-schema validation path. If such a queued call is later changed into a valid action, the binding changes and execution still fails closed.

Core now uses this call-specific binding at all three lifecycle points as one atomic migration:

1. `TurnRunner` creates `session.pending_bindings` from the exact sampled call;
2. `AgentRuntime.resume_approval()` rebuilds the same action identity before accepting approval;
3. `_process_pending_tools()` validates the same identity immediately before preparation/execution.

There is no parallel pending-action store in `SandboxAgentRuntime`.

## Sandbox escalation composition

Sandbox escalation remains an execution-attempt decision, not a different action identity. The same original call, frozen Step and pending action binding survive the transition from the initial sandboxed attempt to the explicitly approved escalation attempt.

The escalation scope is opened only when the approved call executes, after Core has validated the action binding. A later model-generated exec therefore returns to its own initial action/attempt state.

## Upgrade behavior

Because typed exec identities use process-local HMACs, restarting Loom already invalidates pending exec approvals by design. This branch also changes the durable key shape for exec from generic tool binding to call-specific action binding. A pending exec approval created by an older runtime therefore fails closed and must be sampled/approved again.

Untyped tools keep their exact legacy `binding_digest()` value, so this migration does not invalidate their pending bindings merely because the runtime gained typed exec actions.

## Remaining boundary

The next useful alignment step is no longer Core binding plumbing. It is to extend the structured action model only where execution semantics justify it—for example apply-patch, MCP or network approval—while keeping each action type canonical and secret-minimized.

Separately, the repository still lacks trustworthy post-launch Bubblewrap/MXC containment-denial classification for ordinary process failures; typed sandbox escalation must continue to activate only from a proven structured denial rather than stderr guessing.
