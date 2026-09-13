# Window02 seal marker

Window: Exec / Sandbox / Approval / Permissions parity

Branch: `codex-approval-sandbox-parity-v1`

Stack base: `codex-apply-patch-action-v1@3128259298f28383a71c2b8f74f2c7fc10576dc3`

Final audited Codex main: `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`

Status: **SEALED** at the contract/audit level.

Seal conditions satisfied:

- ToolOrchestrator centrally owns approval, sandbox selection, attempt, denial retry, and cache/bypass decisions;
- exec reusable approval keys and durable pending-action integrity are separate;
- apply-patch reusable approval keys are environment + path rather than whole-patch digest;
- `SandboxPermissions` supports default, full escalation, and scoped additional-permission attempts;
- fresh `with_additional_permissions` is limited to the Codex `OnRequest` path until Loom has environment-scoped preapproved grant authority;
- `require_escalated` is a first-attempt override rather than an unconditional second approval stage;
- only typed/centrally classified sandbox denial can trigger retry planning;
- retry is at most once and cannot bypass `SandboxPolicy.REQUIRED`;
- session approval reuse is process-local and exact-key scoped;
- cancellation and malformed actions fail closed before side effects.

Explicit partials remain recorded in `docs/codex-approval-sandbox-parity-window02-final.md`, especially environment-scoped granted permissions, arbitrary denied-read preservation, the full exec-policy engine, retained stdin approval, and network approval service integration.

Validation state at seal: `contract committed, CI not executed`.

This seal freezes the Window02-owned v1 contract. It does not claim full Codex permission-profile parity and does not authorize merging the stacked Draft PR solely on the basis of this marker.
