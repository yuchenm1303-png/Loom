# Codex-style orchestration alignment

This branch starts moving Loom's tool execution toward the current Codex runtime ownership model without rewriting Loom in Rust.

## What this stage changes

- `ToolOrchestrator` is now both the preparation boundary and the handler execution boundary for the default sandbox-backed runtime stack.
- An approval-required prepared call cannot execute unless the runtime explicitly carries an approval grant into the execution boundary.
- A denied prepared call cannot execute even if a caller mistakenly supplies an approval flag.
- Pending approvals now distinguish the initial authorization stage from a future sandbox-escalation retry stage.
- Approval-stage metadata is durable and old session snapshots remain compatible by defaulting missing stage data to `initial`.
- CI no longer references the removed Qt desktop launcher/tests, and the general Linux test job installs the browser extra required by the current suite.

## What this stage intentionally does not claim

This stage does **not** yet retry a failed sandboxed process without containment. It creates the single execution choke point and durable approval representation required to implement that safely next.

The next stage is:

1. represent sandbox-policy rejection as a typed execution outcome;
2. keep the original prepared call and frozen step binding;
3. create a `sandbox_escalation` approval only when policy permits an escalated attempt;
4. retry at most once after explicit approval;
5. record the first and escalated attempts distinctly in observable events.

Network approval remains a later layer on the same orchestration boundary.
