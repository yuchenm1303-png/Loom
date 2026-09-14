# Window02 final audit addendum

This addendum is the final acceptance note for the Exec / Sandbox / Approval / Permissions window. Where it conflicts with earlier wording in `codex-approval-sandbox-parity-v1.md`, this file is authoritative.

## Final upstream baseline

- Final audited Codex `main`: `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.
- Previous reference: `1715e55076737158ba61d43158ede504de6d4ce1`.
- The single intervening commit is Windows Desktop ownership / ACL / provisioning work and does not change the approval/sandbox contracts used by this window.

## Final policy correction

Current Codex accepts a fresh `with_additional_permissions` exec request only under `AskForApproval::OnRequest`, unless the requested authority is already preapproved through environment-scoped granted permissions.

Loom does not yet carry Codex's per-environment granted session/turn permission authority. Therefore Window02 now fails closed for a fresh `with_additional_permissions` request under `unless-trusted`, `granular`, or `never`, instead of pretending that a normal approval creates equivalent preapproved authority. Model-facing capability guidance follows the same rule.

This is intentionally stricter than inventing authority Loom cannot prove.

## Required A-F answers

### A. Is `typed sandbox denial -> SANDBOX_ESCALATION -> no-sandbox retry` Codex-equivalent?

No. Codex resolves approval requirements and `SandboxPermissions` before the first attempt. `require_escalated` can select an unsandboxed first attempt. A typed sandbox denial may permit one retry, but a fresh review is policy/context dependent. Loom's old unconditional second approval model was replaced. `SANDBOX_ESCALATION` remains only a Loom durable event label when a retry genuinely needs fresh review.

### B. Does Codex prefer scoped additional permissions over full bypass?

Yes, when the requested authority can be represented by `with_additional_permissions`. It preserves sandbox containment and widens only approved filesystem/network authority. Full `require_escalated` is a separate, broader request. A *fresh* additional-permission request is currently an `OnRequest` path; other policies need already-granted environment authority, which Loom does not yet implement.

### C. When does retry bypass another review?

A prior approval can be reused for the one sandbox-denial retry when Codex's retry decision says the action is already approved and neither strict review nor structured network approval requires a fresh decision. Strict review and network approval context force fresh review. `OnRequest` does not turn an ordinary sandbox denial into a generic full-escalation prompt; its special retry-review path is structured network approval. Loom implements this supported matrix and never performs a third retry.

### D. What does Loom `SandboxPolicy.REQUIRED` map to?

It is a Loom platform fail-closed adapter for the Codex rule that unsandboxed execution is not permitted. Under REQUIRED, missing containment fails before execution and full/no-sandbox bypass is rejected. This is deliberately stronger/coarser than Codex's full permission-profile model; Loom does not yet model arbitrary denied-read entries, so REQUIRED must not be described as complete denied-read parity.

### E. Is ordinary stderr or a non-zero exit enough to classify sandbox denial?

No. Loom requires an enforced sandbox, a failed command, and a recognized sandbox-denial marker in command stdout/stderr/aggregated output (or an explicit typed sandbox failure). Harness text is excluded. This follows Codex's executor-managed denial heuristic. Codex also has backend-specific Linux seccomp signal handling; Loom's current Bubblewrap/MXC adapters do not claim that backend-specific case.

### F. What enters reusable exec approval identity?

The reusable exec approval key is: `environment_id`, executable, canonical command, cwd, tty, `sandbox_permissions`, and `additional_permissions`. Call id, stdin, environment-variable values, timeout, and other durable execution details do not redefine reusable approval equivalence. Loom may keep stricter data in its durable pending-action binding for integrity, but that binding is not the approval cache key.

For apply-patch, reusable approval remains `environment_id + canonical path`; exact patch content remains part of the pending action, not the reusable cache key.

## Accepted adapted/partial areas

The following are explicit remaining gaps, not parity claims:

1. **Environment-scoped granted permissions.** Codex stores/merges granted session and turn additional permissions by `environment_id`. Loom currently has only fresh per-call scoped requests and an in-process ApprovedForSession action cache. Until the TurnState/environment authority is ported, non-OnRequest fresh additional permissions fail closed.
2. **Denied-read preservation.** Codex prevents full unsandboxed escalation when active filesystem policy contains denied-read restrictions and preserves those restrictions across permission merges. Loom's current coarse sandbox profile does not represent arbitrary denied-read entries. `SandboxPolicy.REQUIRED` is a fail-closed platform policy, not a substitute claim of full denied-read parity.
3. **Exec-policy engine/amendments.** Codex danger heuristics, persisted prefix rules, and amendment decisions are richer. Loom keeps a conservative direct-argv/canonicalization subset and does not claim complete exec-policy parity.
4. **Existing-process stdin/write approval.** Codex retains launch permissions and has dedicated write-stdin review semantics. Loom still uses its generic process-control approval path.
5. **Network approval service.** Window02 exposes the structured retry boundary, while the network service belongs to the network/MCP integration window.
6. **Remote/multi-environment authority.** Loom currently uses `environment_id="local"`; the types include environment identity so a future environment owner can provide real values.
7. **Linux permission-profile breadth.** Bubblewrap currently provides broad read visibility and no Codex-equivalent network proxy, so only the enforceable subset of additional permissions is claimed.

## Accepted contracts

- ToolOrchestrator owns approval requirement, sandbox attempt selection, typed-denial retry planning, and approval cache reuse.
- `use_default`, `require_escalated`, and `with_additional_permissions` are distinct typed requests.
- `require_escalated` is an explicit first-attempt override, not an automatic second-stage state.
- only typed/centrally classified sandbox denial can enter retry planning;
- at most one sandbox-denial retry is possible;
- no retry mutates global/ambient sandbox policy;
- REQUIRED never silently falls back to no sandbox;
- ApprovedForSession cache is process-local/session-local and keyed by Codex-style reusable keys;
- durable action integrity and reusable approval identity remain separate;
- cancellation invalidates late approval before side effects;
- malformed actions do not gain approval-cache bypass.

## Validation state

GitHub Actions associated with the pre-final head created jobs with `steps=null` and `logs_url=null`; no runner executed the test suite. The correct validation statement remains:

`contract committed, CI not executed`

This is neither a green-CI claim nor evidence of pytest/build failure.

## Window02 status

With the fresh additional-permission policy conflict corrected and the unported authority surfaces explicitly fail-closed/documented, Window02 is accepted at the contract/audit level and may be sealed. This seal does not assert full Codex permission-profile parity and does not authorize merging the stacked Draft PR without executable integration evidence.
