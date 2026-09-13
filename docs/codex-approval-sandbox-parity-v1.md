# Codex approval / sandbox parity v1

Audit baseline: `openai/codex` main at `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.
The immediately preceding audited commit (`1715e550...`) differs only in unrelated
Windows desktop provisioning files, so the approval/sandbox sources used here are
unchanged across those two commits.

## Source map

- Codex `core/src/tools/orchestrator.rs` -> Loom `agent_runtime/orchestrator.py`
- Codex `core/src/tools/sandboxing.rs` -> Loom `permissions.py`, `sandbox_attempt.py`, `sandbox_runtime.py`
- Codex `core/src/tools/approvals.rs` -> Loom `approval_actions.py`
- Codex `core/src/tools/runtimes/unified_exec.rs` -> Loom `execution_action.py`, process runtime boundary
- Codex `core/src/tools/runtimes/apply_patch.rs` -> Loom `apply_patch_action.py` approval identity/cache only
- Codex `sandboxing/src/policy_transforms.rs` -> Loom `sandbox_additional_permissions.py`
- Codex `sandboxing/src/denial.rs` -> Loom `sandbox_denial.py`
- Codex `core/src/command_canonicalization.rs` -> Loom `command_approval.py`

## Replaced Loom self-design

The old #121 state machine treated every typed first sandbox denial as a new
`SANDBOX_ESCALATION` approval followed by one no-sandbox retry. Codex does not.
The request first carries `SandboxPermissions` (`use_default`,
`require_escalated`, or `with_additional_permissions`), approval requirements are
resolved before the first execution attempt, and `require_escalated` can select an
unsandboxed *first* attempt. A sandbox denial may lead to one retry, but whether a
fresh review is required depends on approval policy, prior approval, strict review,
and structured network context.

`SANDBOX_ESCALATION` remains only as Loom's durable pending-review event kind for a
fresh retry review. It no longer defines the overall execution model.

## Durable binding vs approval reuse

#122/#123 action digests remain intentionally stricter than Codex approval-cache
keys. Loom uses those digests to prove that a durable pending action has not
changed while review is outstanding. Reusable approval identity is separate:

- exec: environment id + executable + canonicalized command + cwd + tty +
  sandbox permissions + additional permissions;
- apply_patch: environment id + canonical path, one key per file.

Call ids are approval-event identity, not reusable cache identity. Environment
variable values and stdin remain part of Loom's pending-action integrity binding
but are not Codex approval-cache fields. Only `ApprovedForSession` is retained in
the session cache.

## Sandbox retry contract

A retry is considered only for a typed/centrally-classified sandbox denial. Loom
ports Codex's sandbox-aware denial classifier: the command must actually be under
an enforced sandbox, must fail, and command stdout/stderr must contain one of the
well-known denial markers. Harness text such as `sandbox=bubblewrap` is excluded
from classification.

There is at most one retry. `SandboxPolicy.REQUIRED` forbids a no-sandbox retry.
Typical `unless-trusted` execution that was already reviewed can reuse that review
for the one retry; strict auto-review and structured network-denial review require
a fresh review. `on-request` does not turn an ordinary sandbox denial into a new
full-escalation prompt; its special retry-review route is reserved for structured
network approval context.

## Additional permissions

`with_additional_permissions` remains sandboxed. Approved filesystem grants are
materialized into the active Bubblewrap/MXC plan. Bubblewrap already exposes the
host read-only, so only additional write binds widen Linux authority; protected
Loom control-plane paths are reasserted read-only. MXC extends approved read/write
paths and enables outbound network only when `network.enabled=true`; ingress stays
denied. Missing/unprovable grants fail closed. Full sandbox bypass remains a
separate `require_escalated` request.

## Loom product adapter

Loom's product-level `PermissionMode.APPROVAL` is mapped to Codex
`unless-trusted`, because the product preset promises that unmatched commands ask
for approval. `PermissionMode.WORKSPACE` remains the Codex-style on-request path
where ordinary sandboxed commands can run without a prompt.

## Deliberate remaining gaps

- Codex exec-policy amendments (`ApprovedExecpolicyAmendment`) persist prefix
  rules. Loom carries a proposed prefix in the action but does not yet own a
  Codex-compatible exec-policy rule store, so it does not claim amendment parity.
- Codex has special retained-policy approval semantics for writing stdin to an
  existing process. Loom still routes process-control tools through its generic
  tool-effect approval model; no false parity claim is made here.
- Network approval orchestration is owned by the MCP/network window. This slice
  exposes the structured `network_approval_context` retry interface and tests its
  re-review semantics, but does not implement the network service.
- The app-server approval response is still boolean. Core supports
  `ApprovedForSession` programmatically, but exposing that choice in product/UI is
  outside this window.
- `TimedOut` and `Abort` are represented as review decisions but are deliberately
  rejected by Loom's legacy boolean resume boundary rather than being mislabeled
  as ordinary denial. Product/protocol plumbing is still required for exact Codex
  terminal semantics.
