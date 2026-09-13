# Codex-style orchestration alignment

This branch moves Loom's core runtime toward the current Codex ownership model without rewriting Loom in Rust. The target is behavioral compatibility at the runtime-contract level: one model sampling step should observe one immutable execution world, and every later tool decision must remain bound to that world.

## Orchestrator execution boundary

- `ToolOrchestrator` is now both the preparation boundary and the handler execution boundary for the default sandbox-backed runtime stack.
- An approval-required prepared call cannot execute unless the runtime explicitly carries an approval grant into the execution boundary.
- A denied prepared call cannot execute even if a caller mistakenly supplies an approval flag.
- Pending approvals distinguish the initial authorization stage from a future sandbox-escalation retry stage.
- Approval-stage metadata is durable and old session snapshots remain compatible by defaulting missing stage data to `initial`.
- A model-originated `exec` call can no longer silently cross Loom's `SandboxPolicy.AUTO` compatibility fallback when no OS sandbox backend is available. The runtime forces an approval and tells the user that the approved call may run without OS-level containment. `FULL_ACCESS` and an explicitly configured `SandboxPolicy.OFF` remain intentional unsandboxed modes.

## Frozen sampling-step request state

The default runtime now captures a `RequestStateSnapshot` inside each immutable `StepContext`. The captured request state currently includes:

- the system prompt used by the sampling step;
- the resolved project instructions (`AGENTS.override.md` / `AGENTS.md`) visible to that step;
- the user communication language anchor;
- provider-safe model-profile metadata;
- the resolved context/token limits used for request budgeting;
- a secret-free MCP binding identity snapshot.

The request assembler consumes those frozen values instead of reading project instructions, language state, or context limits a second time. Tool-schema pressure planning also reuses the same frozen token budget.

Approval binding version 4 hashes the request-state digest. As a result, changing project instructions, model-profile identity, context limits, or the MCP binding between tool sampling and approval resume fails closed instead of executing an old tool call in a different world.

The MCP snapshot deliberately separates identity from availability. It hashes server configuration identity and MCP tool binding keys, and records protocol/server/tool-surface identity when available, but it does not copy credential values and does not bind to a transient connected/disconnected boolean.

Legacy/lower-level embedders that construct a `StepContext` without a captured request state retain the previous live-resolution behavior. The production/default runtime uses the frozen path.

## Exec action/environment approval identity

`exec` now receives a narrower action-specific binding in addition to the common step binding:

- the exact approval arguments must still match the queued `ToolCall` before an approved action may execute;
- the resolved child environment is included in approval identity through a process-local HMAC fingerprint;
- the child environment map itself is not persisted in session state or events;
- only the new-process `exec` tool uses this environment fingerprint. `exec_wait`, `exec_write`, and `exec_resize` operate on an already-created process and are not invalidated by later host-environment changes;
- if inherited environment state such as `PATH` changes while an `exec` approval is waiting, approval resume fails closed;
- because the HMAC key is process-local, a Loom process restart intentionally invalidates any pending pre-restart `exec` approval. The command must be sampled/approved again in the new runtime process.

This is deliberately narrower than putting the full host environment into every `StepContext`. Environment changes should affect tools whose execution semantics actually depend on launching a new child process, not unrelated actions such as file edits or MCP calls.

## CI cleanup and current infrastructure limitation

CI no longer references the removed Qt desktop launcher/tests, and the general Linux test job installs the browser extra required by the current suite.

GitHub Actions currently fails before any runner step begins: jobs report `steps: []` and `runner_id: 0`, while the job-log endpoint returns `BlobNotFound`. The same failure shape existed on `main`, so a red Actions run currently does not constitute a pytest/build result for this branch.

## What this stage intentionally does not claim

This stage does **not** yet infer that an arbitrary non-zero command exit was caused by the sandbox, and it does not retry ordinary command failures outside containment. Loom's current bwrap/MXC path does not expose a trustworthy typed denial for every post-launch filesystem failure, so guessing from stderr such as `Permission denied` would be unsafe.

It also does not yet make Loom's StepContext as broad as Codex's current StepContext. Important remaining alignment work includes:

1. move request-state capture lower into the core runtime so lower-level runtime compositions do not need a compatibility fallback;
2. replace the current exec-specific environment fingerprint with a first-class typed execution-action snapshot when the core runtime can carry action objects end to end;
3. make MCP binding a first-class typed object rather than canonical JSON inside request state;
4. represent sandbox-policy rejection as a typed execution outcome where the backend can prove it;
5. create a `sandbox_escalation` approval only when policy permits an escalated attempt and retry at most once;
6. add network approval on the same orchestration boundary.

The rule remains: do not imitate Codex by name alone. Port the observable state transition and fail-closed invariant, then lock it with a Loom test.
