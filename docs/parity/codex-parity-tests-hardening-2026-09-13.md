# Codex parity tests / CI / hardening report — 2026-09-13

## Scope and baselines

This report is the validation artifact for the `codex-parity-tests-hardening-v1` branch. It intentionally changes tests and parity documentation only; no Loom production runtime file is changed by this branch.

- Loom repository: `yuchenm1303-png/Loom`
- Loom base: `b3dc3db50b117615be59b79806a6fd8fd38b02a8`
- Codex repository: `openai/codex`
- Codex `main` re-read at start of work: `1715e55076737158ba61d43158ede504de6d4ce1`
- Codex commit date: 2026-09-13
- Codex commit subject: `Bind direct tool-call metadata to invocation outputs (#45185)`

The comparison target is observable contract/state-machine behavior, not Rust syntax or product UI.

## Codex source → contract → Loom counterpart → gap → port plan

### P0 — safety / lifecycle

| Codex source / test | Protected contract | Loom counterpart | Gap | Port status |
| --- | --- | --- | --- | --- |
| `codex-rs/core/src/session/step_activation_tests.rs::submitted_sparse_updates_preserve_captured_steps_and_ordering` | A submitted task keeps its captured step and ordering. | `tests/test_permission_snapshot_alignment.py`; `app/agent_runtime/turn_runner.py` immutable per-sample `StepContext` | Managed-authority activation semantics are broader in Codex. | Existing partial coverage; do not claim equivalence. |
| `step_activation_tests.rs::delayed_activation_does_not_retarget_a_task` | Delayed activation cannot silently retarget a task to newer state. | Frozen `StepContext`, `pending_bindings` and approval binding digest. | Loom has no direct activation-state-machine analogue. | Partial. |
| `step_activation_tests.rs::delayed_activation_rechecks_live_managed_authorization` | Captured task identity is retained while live managed authorization is rechecked. | Permission snapshot + approval binding revalidation. | Full managed-policy authority model not present. | Partial. |
| `step_activation_tests.rs::instruction_refresh_serializes_reads_and_releases_on_cancellation` | Instruction refresh is serialized and cancellation releases waiters. | AGENTS/skill snapshot tests in `tests/test_alignment_reliability.py`. | No direct serialized refresh/cancellation contract located in Loom. | Missing direct parity test/capability evidence. |
| `codex-rs/core/src/tools/approvals_tests.rs::approval_resolution_aborts_turn_when_approval_is_aborted` | Aborted approval aborts the owning turn rather than executing. | Durable pending approval + cancellation in `runtime.py`. | Needed an explicit late-resolution regression. | Added `test_cancel_waiting_approval_invalidates_late_resolution`. |
| `codex-rs/core/tests/suite/approvals.rs` permission/sandbox cases | Approval is coupled to sandbox/permission state; escalation does not silently broaden authority. | `tests/test_permission_snapshot_alignment.py`, `tests/test_sandbox_runtime.py`. | Codex amendment/reviewer/escalation surface is richer. | Partial; new drift/action tests added. |
| `approvals_tests.rs::approval_resolution_rejects_denied_network_policy_amendment` | A denied network amendment cannot become authority through approval resolution. | Loom network/sandbox policy is separate and still evolving. | Exact network amendment semantics missing on base. | Missing / blocker. |
| `approvals_tests.rs::approval_resolution_rejects_mcp_policy_amendment` | MCP policy amendment cannot silently widen authority. | MCP tool binding identity/digest. | No Codex-style MCP policy amendment state machine. | Partial. |
| `codex-rs/core/tests/suite/approvals.rs` multi-call approval flows | Completed tool outputs remain observations while a later call waits for approval; resume continues the same turn. | Core pending tool queue and durable approval. | No single cross-module regression existed on base. | Added `test_multiple_tool_calls_pause_at_approval_then_resume_with_observations`. |
| Current direct metadata suite: repeated call IDs | Call IDs may repeat without losing per-invocation output association. | Ordinary Loom tool history is call-id based but has no Codex direct metadata binding layer. | Execution should not deduplicate repeated IDs; metadata attribution still missing. | Added shallow execution regression; direct metadata parity remains Missing. |
| Codex abort/cancellation tests + approval tests | Cancellation wins races and cannot be undone by a late response. | `tests/test_alignment_reliability.py` cancellation coverage. | Explicit late approval resolution case was absent. | Added regression. |
| Codex exec/apply-patch approval scenarios | Approval authorizes the exact action, once. | Loom `exec`, `apply_patch`, pending argument/binding state. | Needed real-tool cross-module evidence rather than synthetic tools. | Added three action-integrity tests. |

### P1 — context / MCP / protocol

| Codex source / test | Protected contract | Loom counterpart | Gap | Port status |
| --- | --- | --- | --- | --- |
| `codex-rs/core/src/compact_tests.rs::local_compaction_respects_tool_metadata_state` | Local compaction preserves or strips executed-tool metadata according to live feature state without corrupting ordinary output metadata. | `tests/test_context_budget_codex_compaction.py` | Loom base has no `ExecutedToolCallMetadata` equivalent. | Missing; production capability required before faithful test port. |
| `codex-rs/core/tests/suite/direct_tool_metadata.rs::direct_call_metadata_during_compaction_respects_provider_support` | Direct-call metadata survives/strips across local vs remote compaction according to provider support and feature state. | Loom compaction tests cover summary/budget/history, not host-owned direct metadata. | Exact capability absent. | Missing / blocker. |
| `direct_tool_metadata.rs::direct_function_and_tool_search_mark_complete_attempts` | Valid, malformed, tool-search and budget-pruned direct attempts have correct completeness metadata; request metadata is budgeted. | Loom malformed response retry + tool observations. | No host-owned direct-call metadata budget/completeness state. | Missing / blocker. |
| Current Codex commit `#45185` direct metadata binding tests | Tool-call metadata is bound to invocation output, not merely keyed by call ID; budget is released on completion/drop. | No corresponding Loom metadata ledger. | Major semantic gap, especially for repeated call IDs. | Missing. |
| `codex-rs/codex-mcp/src/binding_tests.rs::prepared_call_keeps_captured_connection_and_authority_after_refresh` | Prepared MCP call retains captured connection/config/authority through refresh. | `tests/test_mcp_runtime.py`, `tests/test_mcp_configured_runtime.py`, execution binding digest. | Loom base has no catalog revision/refresh prepared-call state. | Missing. |
| `binding_tests.rs::prepared_call_is_rejected_after_catalog_refresh` | A stale prepared MCP call is rejected before preparation/execution after catalog refresh. | Static/configured MCP binding. | No catalog revision refresh contract. | Missing / blocker. |
| `binding_tests.rs::stale_prepared_call_does_not_run_preparation` | Stale-call rejection occurs before side effects. | Binding digest fail-closed for approval. | Not equivalent to catalog-preparation guard. | Missing. |
| `binding_tests.rs::preparation_holds_catalog_authority_until_it_finishes` | Refresh cannot overtake an in-progress preparation that owns catalog authority. | No identified Loom analogue. | Missing serialization/authority mechanism. | Missing. |
| `codex-rs/core/src/tools/spec_plan_tests.rs` | Visible tool specs/namespaces/mode/worker controls form a deterministic tool plan. | Loom `ToolRegistry`, `ToolRouter`, Tool Search schema planning. | No full Codex tool-plan parity inventory yet. | Partial. |
| `codex-rs/app-server/tests/suite/v2/**` approval/protocol lifecycle | Protocol reconnection cannot orphan or retarget durable work; approval remains attached to exact request/thread generation. | Durable thread ID + call ID and `approval/respond`. | Loom protocol is not Codex v2 wire-identical; generation mechanics differ. | Adapted behavior test added. |
| Core resumed-history tests such as `core/tests/suite/agents_md.rs`, `fork_thread.rs`, `review.rs` | Resumed threads reconstruct history/state without changing task semantics. | `tests/test_runtime_v2_durable_thread.py`, `tests/test_alignment_reliability.py`. | Full Codex rollout/history format differs. | Adapted partial coverage. |

### P2 — edge / compatibility

| Codex source / test | Protected contract | Loom counterpart | Gap | Port status |
| --- | --- | --- | --- | --- |
| `approvals_tests.rs::non_utf8_cwd_preserves_approval_routing` | Edge-path encoding does not redirect approval ownership. | Workspace normalization tests, Windows/path tests. | Exact non-UTF-8 route not ported. | Missing edge case. |
| `approvals_tests.rs::explicit_mcp_reviewer_override_takes_precedence_over_action_context` | Explicit reviewer authority wins over ambient action context. | No reviewer override analogue located. | Product/runtime semantic difference. | Missing unless Loom adopts reviewer authority. |
| Direct metadata malformed-call coverage | Malformed tool arguments still produce correctly attributed completion/error metadata. | Malformed provider/tool validation tests. | Ordinary error path exists, metadata attribution layer absent. | Partial at tool execution, Missing at metadata layer. |
| Repeated direct call ID after compaction | A reused ID after compaction is associated with the new invocation, not old metadata. | Added sequential repeated-ID execution test. | Metadata association remains unimplemented. | Partial. |

## Required cross-module scenario status

1. **model sample → multiple tool calls → approval → observation → next step** — new contract added: `test_multiple_tool_calls_pause_at_approval_then_resume_with_observations`.
2. **settings/tool catalog drift while waiting approval** — permission mode and tool binding drift are now fail-closed in `test_waiting_approval_fails_closed_on_permission_or_binding_drift`; full MCP catalog revision/refresh remains Missing.
3. **sandbox denial + permission/escalation** — existing sandbox tests prove REQUIRED fail-closed and recovery behavior; new denied-exec test proves user denial creates an observation with no process side effect. Codex-style typed containment-denial → one-shot escalation is not present on the reviewed base and remains Partial.
4. **malformed tool call** — existing malformed provider/tool validation coverage in `tests/test_alignment_reliability.py`; Codex direct metadata attribution for malformed attempts is Missing.
5. **cancellation race** — existing model cancellation coverage plus new late-approval invalidation test.
6. **restart/recovery** — existing approval-binding restart and durable-thread recovery tests; full Codex rollout equivalence is not claimed.
7. **compaction with tool metadata** — Missing. Codex source located precisely in `compact_tests.rs` and `direct_tool_metadata.rs`; Loom lacks the metadata capability required for a faithful test.
8. **MCP binding refresh** — Missing. Codex prepared-call/catalog revision state machine has no Loom base counterpart.
9. **app-server reconnect during approval** — added adapted contract: `test_reconnected_controller_can_resolve_existing_approval`.
10. **two sessions concurrently** — added `test_two_sessions_can_sample_concurrently_without_cross_session_serialization`; existing execution-lease tests cover same-session ownership separately.
11. **same call id reuse** — added ordinary execution regression `test_reused_call_id_executes_each_sample_once_without_deduplicating`; current Codex direct metadata association remains Missing.
12. **patch/exec action approval integrity** — added real `exec` and `apply_patch` tests plus denied-exec no-side-effect test.

## Tests added by this branch

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

Total: **9 new parity/regression tests**.

## Execution evidence

### Local execution attempt

An executable checkout was attempted with:

```text
git clone --depth 1 --branch codex-parity-tests-hardening-v1 https://github.com/yuchenm1303-png/Loom.git /tmp/loom-parity
```

The execution environment failed before checkout with:

```text
fatal: unable to access 'https://github.com/yuchenm1303-png/Loom.git/': Could not resolve host: github.com
```

Therefore no local `pytest` result exists for these new tests. This is an environment/network limitation, not a test pass or test failure.

### GitHub Actions evidence

Reviewed `.github/workflows/ci.yml`: it uses ordinary `ubuntu-latest` / `windows-latest` labels and normal checkout/setup/install/test steps. No concrete YAML syntax failure was observed, so this branch does **not** rewrite the workflow speculatively.

Observed run `34756502512`:

- jobs were created and completed as `failure` within seconds;
- inspected jobs had no steps (`steps: []` / `steps: null` at run-job level);
- `test` job `103721658556` returned no steps;
- log retrieval for that job returned `404 BlobNotFound`.

Observed `main` run `34748733495` at Loom base `b3dc3db50b117615be59b79806a6fd8fd38b02a8`:

- all Linux/Windows jobs showed the same pre-step failure shape;
- `test` job `103701262426` had no steps.

Historical run `34114209121` on the same repository/workflow completed successfully, so `ubuntu-latest` / `windows-latest` are not intrinsically invalid labels for this repository.

Repository Actions policy/billing/quota/account endpoints were not available through the connected GitHub surface. Therefore the exact platform gate is **unverified**. Billing, quota, policy and account restriction remain hypotheses only and must not be presented as the root cause without platform evidence.

**Conclusion: `contract committed, CI not executed`.** The current red checks are not evidence that pytest, npm, Windows sandbox tests, or the new parity tests failed.

## Parity scorecard

| Domain | Rating | Evidence / reason |
| --- | --- | --- |
| Turn/Step | **Partial** | Loom has immutable per-sample StepContext and durable pending state, but not the full Codex step-activation/managed-authority state machine or serialized instruction-refresh semantics. |
| Approval/Sandbox | **Partial** | Strong fail-closed permission snapshot, durable approval, binding validation and sandbox policy tests; Codex amendment/reviewer/escalation semantics are broader. |
| Exec | **Partial** | Strong process lifecycle and new exact-action approval test on base; stacked typed-action/orchestrator work is not merged/validated. |
| Patch | **Partial** | Atomic patch/preimage/diff behavior exists and exact pending patch approval is newly tested; typed action identity is still a draft stacked PR. |
| Context/Compaction | **Partial** | Auto/length compaction, budgets, language anchor and summaries are tested; current Codex direct-tool metadata compaction semantics are Missing. |
| Instructions | **Partial** | AGENTS/skill snapshot and restart coverage exist; Codex serialized refresh + cancellation authority is not proven. |
| MCP | **Partial** | Configuration/binding/credential safety exists; prepared-call catalog revision/refresh state machine is Missing. |
| Network | **Partial** | Sandbox/network policy pieces exist, but base does not demonstrate current Codex amendment semantics or completed OS egress propagation. |
| App Server | **Partial** | Durable thread/approval protocol plus new reconnect contract; wire protocol and reconnect-generation model are an adapted Loom implementation, not Codex v2 equivalence. |
| Recovery | **Partial** | Durable queue/restart/torn-log/approval-binding recovery tests are substantial; Codex rollout/history representation and all recovery edges are not fully mapped. |
| Concurrency | **Partial** | Existing same-session execution lease plus new two-session non-global serialization regression; full Codex governance concurrency is not ported. |
| Tests/CI | **Partial** | High-value parity contracts committed, but repository Actions currently fails before runner steps and these new tests have no executed result. |

No domain is labeled `Codex-equivalent` in this report without end-to-end source/test evidence.

## Current blockers to mature Codex-runtime parity

1. **Executed direct tool-call metadata (`#45185`)**: Loom lacks Codex's output-bound metadata ledger, completeness markers, pending/request budgets and cancellation/drop release semantics. Repeated call IDs make call-id-only association insufficient.
2. **Metadata-aware compaction**: no faithful Loom port is possible until the above metadata representation exists.
3. **MCP catalog revision / prepared-call authority**: refresh must reject stale prepared calls before preparation side effects while preserving captured connection/authority for in-flight work.
4. **Step activation / managed authority**: Loom's immutable step snapshot covers part of the contract, not Codex's delayed activation + live managed authorization + serialized instruction refresh state machine.
5. **Approval amendment / network authority**: exact Codex network/MCP amendment rejection and reviewer authority are not represented on the reviewed base.
6. **Network enforcement completion**: policy classification is not equivalent to OS-level egress enforcement.
7. **CI runner/platform gate**: tests cannot be called green until a runner actually executes steps.

## Open PR merge guidance

### Absolute hold while validation is unavailable

- **#121 — `Align runtime execution boundary with Codex orchestration`**: touches P0 approval/sandbox/step authority and explicitly reports pre-runner CI. It should not merge until the parity suite and its own tests actually execute. Its own PR body also lists remaining StepContext, MCP typing, backend denial, static approval snapshot and network gaps.
- **#122 — `Introduce typed exec action identity`**: draft stacked on #121, changes approval/action binding identity. Do not merge before #121 is validated and the stacked tests execute.
- **#123 — `Add typed apply-patch action identity`**: draft stacked on #122, changes patch approval identity. Do not merge before its parent stack is validated and the tests execute.
- **#120 — `feat(agent): add Codex-style exec and network policy`**: safety-relevant network/exec policy work. Its own scope says OS sandbox egress propagation is a follow-up. Do not merge it as a claim of completed Codex network parity; with CI currently not executing, this report recommends holding the safety-sensitive change until executable validation exists.

### Outside this window / no parity-green endorsement

- **#119 — `fix(reasoning): align effort controls with Codex semantics`** is a reasoning/provider-state PR rather than the core approval/MCP/exec focus of this window. It is a draft and also reports pre-runner CI. This report does not certify it; absence of a hold finding here is not a merge approval.

## Acceptance summary

- Codex baseline re-read and pinned: `1715e55076737158ba61d43158ede504de6d4ce1`.
- High-value Codex source/test semantics inventoried before test implementation.
- 9 new cross-module parity contracts committed; 0 production files changed.
- CI failures were investigated as runner/pre-step failures rather than mislabeled pytest failures.
- Current direct metadata + compaction and MCP catalog-refresh gaps are explicitly classified as Missing rather than papered over with invented tests.
- Scorecard remains conservative: no `Codex-equivalent` labels without executable and source-backed evidence.
- **Validation state: `contract committed, CI not executed`.**
