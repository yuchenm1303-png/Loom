# Codex parity tests / CI / hardening report — 2026-09-13

## Scope and baselines

Validation branch: `codex-parity-tests-hardening-v1`. This branch changes tests and parity documentation only; **0 Loom production runtime files are changed**.

- Loom base: `yuchenm1303-png/Loom@b3dc3db50b117615be59b79806a6fd8fd38b02a8`
- Codex `main` re-read before implementation: `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1`
- Codex baseline date: 2026-09-13
- Codex baseline subject: `Bind direct tool-call metadata to invocation outputs (#45185)`

The target is observable runtime contract/state-machine behavior, not Rust syntax or Codex product UI.

## Codex parity test inventory

### P0 — safety / lifecycle

| Codex source / test | Contract protected | Loom counterpart | Gap / port decision |
| --- | --- | --- | --- |
| `codex-rs/core/src/session/step_activation_tests.rs::submitted_sparse_updates_preserve_captured_steps_and_ordering` | Submitted work keeps captured step identity/order. | Immutable per-sample `StepContext`, permission snapshot tests. | Existing partial coverage; Codex activation authority is broader. |
| `step_activation_tests.rs::delayed_activation_does_not_retarget_a_task` | Delayed activation cannot retarget work to newer state. | Frozen step + pending binding digest. | Partial; no direct activation-state-machine analogue. |
| `step_activation_tests.rs::delayed_activation_rechecks_live_managed_authorization` | Retain captured task identity while rechecking live managed authority. | Approval/binding revalidation. | Full managed-policy authority model Missing. |
| `step_activation_tests.rs::instruction_refresh_serializes_reads_and_releases_on_cancellation` | Instruction refresh serialized; cancellation releases waiters. | AGENTS/skill snapshot tests. | Direct refresh-lock/cancellation contract Missing. |
| `codex-rs/core/src/tools/approvals_tests.rs::approval_resolution_aborts_turn_when_approval_is_aborted` | Aborted approval cannot execute and aborts owning work. | Durable pending approval + cancel. | Added explicit late-resolution regression. |
| `codex-rs/core/tests/suite/approvals.rs` permission/sandbox matrix | Approval remains coupled to captured permission/sandbox authority. | `test_permission_snapshot_alignment.py`, `test_sandbox_runtime.py`. | Codex amendment/reviewer/escalation semantics are richer. |
| `approvals_tests.rs::approval_resolution_rejects_denied_network_policy_amendment` | Denied network amendment cannot become authority through approval. | Network/sandbox policy pieces. | Exact amendment semantics Missing. |
| `approvals_tests.rs::approval_resolution_rejects_mcp_policy_amendment` | MCP policy amendment cannot silently widen authority. | MCP/tool binding digest. | Codex-style MCP amendment state machine Missing. |
| Codex multi-call approval flows | Earlier tool observations survive while later tool call waits; resume continues same turn. | Pending tool queue + durable approval. | Added direct cross-module regression. |
| Codex cancellation/abort tests | Cancellation wins races; late resolution cannot resurrect work. | Existing cancellation tests. | Added late-approval regression. |
| Codex exec/apply-patch approval behavior | Approval authorizes exact action, once. | Real Loom `exec`/`apply_patch`. | Added real-tool action-integrity tests. |
| Current direct-metadata repeated-call-id regressions | Reused call IDs must not attach old invocation metadata to new output. | Ordinary tool history only. | Added shallow execution regression; direct metadata association remains Missing. |

### P1 — context / MCP / protocol

| Codex source / test | Contract protected | Loom counterpart | Gap / port decision |
| --- | --- | --- | --- |
| `codex-rs/core/src/compact_tests.rs::local_compaction_respects_tool_metadata_state` | Local compaction preserves/strips executed-tool metadata according to feature state. | `test_context_budget_codex_compaction.py`. | `ExecutedToolCallMetadata` equivalent Missing; faithful port blocked. |
| `codex-rs/core/tests/suite/direct_tool_metadata.rs::direct_call_metadata_during_compaction_respects_provider_support` | Direct metadata across local/remote compaction obeys provider support and feature state. | Summary/budget/history compaction tests. | Host-owned direct metadata Missing. |
| `direct_tool_metadata.rs::direct_function_and_tool_search_mark_complete_attempts` | Valid/malformed/search attempts carry correct completeness metadata under request budgets. | Malformed/tool observation tests. | Metadata budget/completeness ledger Missing. |
| Current Codex `#45185` tests | Metadata is bound to invocation output; pending/request budget released on completion/drop/cancel. | No equivalent Loom ledger. | Major Missing capability; repeated IDs make call-id-only association insufficient. |
| `codex-rs/codex-mcp/src/binding_tests.rs::prepared_call_keeps_captured_connection_and_authority_after_refresh` | Prepared call retains captured connection/config/authority during refresh. | Static/configured MCP binding. | Catalog revision/prepared-call state Missing. |
| `binding_tests.rs::prepared_call_is_rejected_after_catalog_refresh` | Stale prepared call rejected before execution after refresh. | Approval binding fail-closed. | Not equivalent; catalog revision Missing. |
| `binding_tests.rs::stale_prepared_call_does_not_run_preparation` | Stale rejection precedes preparation side effects. | No direct analogue. | Missing. |
| `binding_tests.rs::preparation_holds_catalog_authority_until_it_finishes` | Refresh cannot overtake active preparation authority. | No identified analogue. | Missing serialization/authority mechanism. |
| `codex-rs/core/src/tools/spec_plan_tests.rs` | Visible tool specs/namespaces/tool mode form deterministic plan. | `ToolRegistry`, `ToolRouter`, Tool Search planning. | Partial; full spec-plan parity not established. |
| `codex-rs/app-server/tests/suite/v2/**` | Reconnect cannot orphan/retarget durable approval/work. | Durable threadId/callId + `approval/respond`. | Adapted Loom protocol; new reconnect test added. |
| Core resume/history tests (`agents_md.rs`, `fork_thread.rs`, `review.rs`, etc.) | Resume reconstructs history/state without changing semantics. | Durable-thread/recovery tests. | Adapted partial; rollout format differs. |

### P2 — edge / compatibility

| Codex source / test | Contract protected | Loom counterpart | Gap / port decision |
| --- | --- | --- | --- |
| `approvals_tests.rs::non_utf8_cwd_preserves_approval_routing` | Edge path encoding cannot redirect approval ownership. | Workspace/path tests. | Exact non-UTF-8 case not ported. |
| `approvals_tests.rs::explicit_mcp_reviewer_override_takes_precedence_over_action_context` | Explicit reviewer authority beats ambient action context. | No reviewer-override analogue. | Missing unless Loom adopts this authority model. |
| Direct metadata malformed-call coverage | Malformed attempts still get correct output-bound metadata. | Ordinary malformed validation exists. | Tool path Partial; metadata layer Missing. |
| Repeated direct call ID after compaction | New invocation with reused ID gets new metadata. | Added sequential repeated-ID execution test. | Metadata association remains Missing. |

## Required cross-module scenarios

1. **model sample → multiple tool calls → approval → observation → next step** — added `test_multiple_tool_calls_pause_at_approval_then_resume_with_observations`.
2. **settings/tool catalog drift while waiting approval** — added permission/tool-binding drift fail-closed test; full MCP catalog refresh remains Missing.
3. **sandbox denial + permission/escalation** — existing REQUIRED/AUTO/fail-closed tests plus new denied-exec no-side-effect observation; Codex-style trusted typed containment denial → one-shot escalation remains Partial on reviewed base.
4. **malformed tool call** — existing Loom malformed provider/tool coverage; direct metadata attribution Missing.
5. **cancellation race** — existing model cancellation plus new late-approval invalidation.
6. **restart/recovery** — existing durable queue, torn-log, approval-binding restart/recovery coverage; full Codex rollout equivalence not claimed.
7. **compaction with tool metadata** — **Missing**; exact Codex source found in `compact_tests.rs` and `direct_tool_metadata.rs`.
8. **MCP binding refresh** — **Missing**; prepared-call/catalog-revision state machine absent.
9. **app-server reconnect during approval** — added adapted `test_reconnected_controller_can_resolve_existing_approval`.
10. **two sessions concurrently** — added non-global-serialization test; existing same-session execution lease remains separate evidence.
11. **same call id reuse** — added ordinary execution regression; exact Codex direct-metadata association still Missing.
12. **patch/exec action approval integrity** — added real `exec`, real `apply_patch`, and denied-exec tests.

## New tests

`tests/test_codex_parity_lifecycle.py`

- `test_multiple_tool_calls_pause_at_approval_then_resume_with_observations`
- `test_waiting_approval_fails_closed_on_permission_or_binding_drift`
- `test_cancel_waiting_approval_invalidates_late_resolution`
- `test_reused_call_id_executes_each_sample_once_without_deduplicating`
- `test_two_sessions_can_sample_concurrently_without_cross_session_serialization`

`tests/test_codex_parity_app_server.py`

- `test_reconnected_controller_can_resolve_existing_approval`

`tests/test_codex_parity_action_integrity.py`

- `test_exec_approval_executes_exact_pending_action_once`
- `test_apply_patch_approval_executes_exact_pending_patch_once`
- `test_denied_exec_action_becomes_observation_without_side_effect`

Total: **9 tests**. Production files changed by this branch: **0**.

## Execution evidence

### Local execution

Attempted:

```text
git clone --depth 1 --branch codex-parity-tests-hardening-v1 https://github.com/yuchenm1303-png/Loom.git /tmp/loom-parity
```

The available execution environment failed before checkout:

```text
fatal: unable to access 'https://github.com/yuchenm1303-png/Loom.git/': Could not resolve host: github.com
```

Therefore no local `pytest` result exists for these nine tests. This is an environment/network failure, not a passing or failing test result.

### GitHub Actions investigation

`.github/workflows/ci.yml` uses ordinary `ubuntu-latest` / `windows-latest` labels and normal checkout/setup/install/test steps. No concrete YAML syntax error was found; no speculative YAML rewrite was made.

Earlier evidence:

- run `34756502512`: inspected jobs completed `failure` with no steps; `test` job `103721658556` returned `steps: []`; log retrieval returned `404 BlobNotFound`.
- `main` run `34748733495` at base `b3dc3db50b117615be59b79806a6fd8fd38b02a8`: same no-step shape on the inspected test job (`103701262426`).
- historical run `34114209121` succeeded, proving the workflow/standard hosted-runner labels have worked in this repository before.

Fresh evidence from this branch / PR #126:

- workflow run `34758253875` was accepted and created all CI jobs.
- eight jobs, including the general `test` job `103726330800`, immediately completed `failure` with `steps: null` / `logs_url: null`.
- explicit step lookup for `103726330800` returned `steps: []`; its log endpoint returned `404 BlobNotFound`.
- in the **same run**, `windows-desktop-smoke` job `103726330788` acquired a runner: `Set up job`, `actions/checkout@v4`, and `actions/setup-python@v5` completed successfully and `Install desktop extra` entered `in_progress` when observed.

This fresh mixed result materially narrows the diagnosis:

- the workflow is accepted by GitHub, so this is not a workflow parse rejection;
- repository Actions is not globally disabled, because one job in the same run reached real runner steps;
- `windows-latest` is not globally invalid/unavailable, because the desktop job used it successfully;
- the eight zero-step failures are **pre-runner / job-level scheduling-or-eligibility failures**, not pytest/npm/build failures;
- the exact scheduler/platform/account cause remains **unverified**. Billing, quota, policy, concurrency/account restriction, or transient platform scheduling are hypotheses only until GitHub exposes a concrete reason.

The general `test` job did not start, so the nine new parity contracts were not executed by CI.

**Validation statement: `contract committed, CI not executed` (for the parity test suite).** A separate desktop job executing does not make the parity tests passed.

## Parity scorecard

| Domain | Rating | Evidence / reason |
| --- | --- | --- |
| Turn/Step | **Partial** | Immutable per-sample step + durable pending state exist; full Codex activation/managed-authority/serialized-refresh semantics do not. |
| Approval/Sandbox | **Partial** | Strong fail-closed snapshots, durable approval, binding checks and sandbox tests; Codex amendment/reviewer/escalation surface is broader. |
| Exec | **Partial** | Process lifecycle + exact pending-action approval test; stacked typed-action/orchestrator work remains unvalidated. |
| Patch | **Partial** | Atomic patch/preimage/diff + exact pending patch test; typed patch identity is still a draft stack. |
| Context/Compaction | **Partial** | Summary/budget/auto-compaction tests exist; current direct-tool metadata compaction semantics Missing. |
| Instructions | **Partial** | AGENTS/skill snapshot/restart coverage exists; serialized refresh/cancellation authority not proven. |
| MCP | **Partial** | Config/binding/credential safety exists; prepared-call catalog revision/refresh Missing. |
| Network | **Partial** | Policy/sandbox pieces exist; Codex amendment semantics and completed OS egress enforcement not demonstrated on base. |
| App Server | **Partial** | Durable approval protocol + new reconnect test; Loom wire/generation model is adapted, not Codex v2 equivalent. |
| Recovery | **Partial** | Durable restart/torn-log/approval-binding recovery substantial; full Codex rollout/history edges not mapped. |
| Concurrency | **Partial** | Same-session execution lease + new cross-session concurrency test; full Codex governance concurrency not ported. |
| Tests/CI | **Partial** | Contracts committed; parity `test` job fails before first step, while one unrelated desktop job can reach a runner. No parity-test execution result. |

No domain is labeled `Codex-equivalent` without end-to-end source and executed-test evidence.

## Blockers

1. **Executed direct tool-call metadata (`#45185`)** — no Loom output-bound metadata ledger, completeness markers, request/pending budgets, or completion/drop/cancel release semantics.
2. **Metadata-aware compaction** — cannot be faithfully ported before direct metadata representation exists.
3. **MCP catalog revision / prepared-call authority** — stale prepared calls must be rejected before preparation side effects while active preparations retain captured authority.
4. **Step activation / managed authority** — immutable snapshot is not the whole Codex activation/live-managed-auth/serialized-refresh state machine.
5. **Approval amendment / network authority** — exact Codex network/MCP amendment and reviewer semantics are not represented on reviewed base.
6. **Network enforcement completion** — classification/policy is not equivalent to OS-level egress enforcement.
7. **CI job-level pre-runner failure** — exact scheduler/platform cause is unknown, and the parity `test` job still never starts.

## Open PR merge guidance

### Absolute hold

- **#121 — `Align runtime execution boundary with Codex orchestration`**: P0 approval/sandbox/step-authority changes. Its own description acknowledges remaining StepContext/MCP/backend-denial/static approval/network gaps. Do not merge until its tests and this parity suite execute.
- **#122 — `Introduce typed exec action identity`**: draft stacked on #121; changes action/approval binding. Do not merge before parent validation and executable tests.
- **#123 — `Add typed apply-patch action identity`**: draft stacked on #122; changes patch approval identity. Do not merge before parent-stack validation and executable tests.
- **#120 — `feat(agent): add Codex-style exec and network policy`**: safety-relevant exec/network policy. Its own scope says sandbox egress propagation is follow-up. Do not merge it as completed Codex network parity, and do not merge the safety-sensitive change while its validation path is unavailable.

### Outside this window / not certified

- **#119 — `fix(reasoning): align effort controls with Codex semantics`** is mainly reasoning/provider-state work, outside this window's core approval/MCP/exec focus. It is draft and also reports pre-runner CI. This report does not certify it.

## Acceptance summary

- Current Codex baseline re-read and pinned before implementation.
- P0/P1/P2 inventory built from exact Codex test/source files before writing Loom tests.
- 9 high-value cross-module parity contracts committed; 0 production files changed.
- Current direct metadata/metadata-compaction and MCP refresh gaps explicitly left Missing instead of inventing substitutes.
- CI root cause is not mislabeled as pytest failure: the parity `test` job has zero steps/logs, while another job in the same run can reach a runner, proving a job-level pre-runner problem rather than a global workflow parse failure.
- PR #126 remains draft.
- **Final validation state: `contract committed, CI not executed` for the parity suite.**
