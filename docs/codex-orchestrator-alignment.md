# Codex-style orchestration alignment

This branch starts moving Loom's tool execution toward the current Codex runtime ownership model without rewriting Loom in Rust.

## What this stage changes

- `ToolOrchestrator` is now both the preparation boundary and the handler execution boundary for the default sandbox-backed runtime stack.
- An approval-required prepared call cannot execute unless the runtime explicitly carries an approval grant into the execution boundary.
- A denied prepared call cannot execute even if a caller mistakenly supplies an approval flag.
- Pending approvals now distinguish the initial authorization stage from a future sandbox-escalation retry stage.
- Approval-stage metadata is durable and old session snapshots remain compatible by defaulting missing stage data to `initial`.
- A model-originated `exec` call can no longer silently cross Loom's `SandboxPolicy.AUTO` compatibility fallback when no OS sandbox backend is available. The runtime forces an approval and tells the user that the approved call may run without OS-level containment. `FULL_ACCESS` and an explicitly configured `SandboxPolicy.OFF` remain intentional unsandboxed modes.
- CI no longer references the removed Qt desktop launcher/tests, and the general Linux test job installs the browser extra required by the current suite.

## What this stage intentionally does not claim

This stage does **not** yet infer that an arbitrary non-zero command exit was caused by the sandbox, and it does not retry ordinary command failures outside containment. Loom's current bwrap/MXC path does not expose a trustworthy typed denial for every post-launch filesystem failure, so guessing from stderr such as `Permission denied` would be unsafe.

The execution choke point and durable approval representation are now in place for a narrower, typed escalation path when the sandbox layer can prove that containment itself rejected an attempt.

The next stage is:

1. represent sandbox-policy rejection as a typed execution outcome where the backend can prove it;
2. keep the original prepared call and frozen step binding;
3. create a `sandbox_escalation` approval only when policy permits an escalated attempt and the earlier approval did not already cover that boundary;
4. retry at most once after the required approval;
5. record the first and escalated attempts distinctly in observable events.

Network approval remains a later layer on the same orchestration boundary.
