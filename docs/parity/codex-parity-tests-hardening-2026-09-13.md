# Codex parity tests / CI / hardening report — 2026-09-13

## Scope and baselines

Validation branch: `codex-parity-tests-hardening-v1`.

- Loom base: `yuchenm1303-png/Loom@b3dc3db50b117615be59b79806a6fd8fd38b02a8`
- Codex `main` re-read before implementation: `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1`
- Codex baseline date: 2026-09-13
- Codex baseline subject: `Bind direct tool-call metadata to invocation outputs (#45185)`
- Production runtime files changed by this branch: **0**

The comparison target is observable runtime contract/state-machine behavior, not Rust syntax or Codex product UI.

## Codex source → contract → Loom counterpart → gap

### P0 — safety / lifecycle

| Codex source / test | Contract | Loom counterpart | Status |
| --- | --- | --- | --- |
| `codex-rs/core/src/session/step_activation_tests.rs::submitted_sparse_updates_preserve_captured_steps_and_ordering` | Submitted work keeps captured step identity/order. | Immutable per-sample `StepContext`, permission snapshot tests. | Partial |
| `step_activation_tests.rs::delayed_activation_does_not_retarget_a_task` | Delayed work cannot retarget to newer state. | Frozen step + pending binding digest. | Partial |
| `step_activation_tests.rs::delayed_activation_rechecks_live_managed_authorization` | Captured task identity plus live managed auth recheck. | Approval/binding revalidation. | Partial; managed-policy model Missing |
| `step_activation_tests.rs::instruction_refresh_serializes_reads_and_releases_on_cancellation` | Serialized instruction refresh; cancellation releases waiters. | AGENTS/skill snapshot tests. | Missing direct equivalent |
| `codex-rs/core/src/tools/approvals_tests.rs::approval_resolution_aborts_turn_when_approval_is_aborted` | Aborted approval cannot execute. | Durable pending approval + cancel. | Added explicit late-resolution regression |
| `codex-rs/core/tests/suite/approvals.rs` permission/sandbox matrix | Approval stays coupled to captured permission/sandbox authority. | Permission snapshot + sandbox runtime tests. | Partial |
| `approvals_tests.rs::approval_resolution_rejects_denied_network_policy_amendment` | Denied network amendment cannot become authority through approval. | Network/sandbox policy pieces. | Missing exact amendment semantics |
| `approvals_tests.rs::approval_resolution_rejects_mcp_policy_amendment` | MCP policy amendment cannot silently widen authority. | MCP/tool binding digest. | Partial; amendment state machine Missing |
| Codex multi-call approval flows | Earlier tool observations survive while later call waits; resume continues same turn. | Pending tool queue + durable approval. | New parity test added |
| Codex cancellation/abort tests | Cancellation wins races; late resolution cannot resurrect work. | Existing cancellation tests. | New late-approval test added |
| Codex exec/apply-patch approval behavior | Approval authorizes exact action once. | Real Loom `exec`/`apply_patch`. | New real-tool integrity tests added |
| Current direct-metadata repeated-call-id regressions | Reused call IDs must not attach old invocation metadata to new output. | Ordinary tool history. | Execution regression added; metadata association Missing |

### P1 — context / MCP / protocol

| Codex source / test | Contract | Loom counterpart | Status |
| --- | --- | --- | --- |
| `codex-rs/core/src/compact_tests.rs::local_compaction_respects_tool_metadata_state` | Compaction preserves/strips executed-tool metadata according to feature state. | `test_context_budget_codex_compaction.py`. | Missing faithful equivalent |
| `codex-rs/core/tests/suite/direct_tool_metadata.rs::direct_call_metadata_during_compaction_respects_provider_support` | Direct metadata across local/remote compaction obeys provider support and feature state. | Summary/budget/history compaction tests. | Missing |
| `direct_tool_metadata.rs::direct_function_and_tool_search_mark_complete_attempts` | Valid/malformed/search attempts have correct completeness metadata under budget. | Malformed/tool observation tests. | Metadata ledger Missing |
| Current Codex `#45185` tests | Metadata bound to invocation output; pending/request budget released on completion/drop/cancel. | No equivalent Loom ledger. | Missing major capability |
| `codex-rs/codex-mcp/src/binding_tests.rs::prepared_call_keeps_captured_connection_and_authority_after_refresh` | Prepared call retains captured connection/config/authority during refresh. | Static/configured MCP binding. | Missing catalog revision/prepared-call state |
| `binding_tests.rs::prepared_call_is_rejected_after_catalog_refresh` | Stale prepared call rejected before execution after refresh. | Approval binding fail-closed. | Missing exact equivalent |
| `binding_tests.rs::stale_prepared_call_does_not_run_preparation` | Stale rejection precedes preparation side effects. | No direct analogue. | Missing |
| `binding_tests.rs::preparation_holds_catalog_authority_until_it_finishes` | Refresh cannot overtake active preparation authority. | No identified analogue. | Missing |
| `codex-rs/core/src/tools/spec_plan_tests.rs` | Visible tool specs/namespaces/mode form deterministic plan. | `ToolRegistry`, `ToolRouter`, Tool Search. | Partial |
| `codex-rs/app-server/tests/suite/v2/**` | Reconnect cannot orphan/retarget durable approval/work. | durable `threadId`/`callId` + `approval/respond`. | Adapted partial; reconnect test added |
| Core resume/history tests (`agents_md.rs`, `fork_thread.rs`, `review.rs`, etc.) | Resume reconstructs history/state without changing semantics. | Durable thread/recovery tests. | Adapted partial |

### P2 — edge / compatibility

| Codex source / test | Contract | Loom counterpart | Status |
| --- | --- | --- | --- |
| `approvals_tests.rs::non_utf8_cwd_preserves_approval_routing` | Edge path encoding cannot redirect approval ownership. | Workspace/path tests. | Missing exact case |
| `approvals_tests.rs::explicit_mcp_reviewer_override_takes_precedence_over_action_context` | Explicit reviewer authority beats ambient action context. | No reviewer-override analogue. | Missing unless Loom adopts model |
| Direct metadata malformed-call coverage | Malformed attempts still get correctly attributed metadata. | Ordinary malformed validation. | Tool path Partial; metadata Missing |
| Repeated direct call ID after compaction | New invocation with reused ID receives new metadata. | Sequential repeated-ID execution regression. | Partial; metadata Missing |

## Required cross-module scenario status

1. **model sample → multiple tool calls → approval → observation → next step** — added `test_multiple_tool_calls_pause_at_approval_then_resume_with_observations`.
2. **settings/tool catalog drift while waiting approval** — added permission/tool-binding drift fail-closed test; full MCP catalog refresh remains Missing.
3. **sandbox denial + permission/escalation** — existing sandbox fail-closed coverage plus new denied-exec no-side-effect observation; Codex-style trusted containment-denial → one-shot escalation remains Partial on reviewed base.
4. **malformed tool call** — existing Loom malformed provider/tool coverage; direct metadata attribution Missing.
5. **cancellation race** — existing model cancellation plus new late-approval invalidation.
6. **restart/recovery** — existing durable queue, torn-log, approval-binding recovery; full Codex rollout equivalence not claimed.
7. **compaction with tool metadata** — **Missing**; exact Codex sources located in `compact_tests.rs` and `direct_tool_metadata.rs`.
8. **MCP binding refresh** — **Missing**; prepared-call/catalog-revision state machine absent.
9. **app-server reconnect during approval** — added adapted `test_reconnected_controller_can_resolve_existing_approval`.
10. **two sessions concurrently** — added non-global-serialization regression; same-session lease already tested separately.
11. **same call id reuse** — added ordinary execution regression; direct-metadata association still Missing.
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

Total: **9 tests**.

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

Therefore no local pytest result exists for these tests. This is an environment/network failure, not a passing or failing test result.

## CI investigation

### A. Job-level pre-runner failures (unresolved platform cause)

Observed historical/current runs where jobs are created as `failure` with `steps: null` / `logs_url: null`. Explicit step lookup returns `steps: []`, and log retrieval returns `404 BlobNotFound`.

Examples:

- run `34756502512`, `test` job `103721658556` — zero steps, no logs;
- `main` run `34748733495`, `test` job `103701262426` — zero steps;
- PR #126 run `34758253875`, `test` job `103726330800` — zero steps, no logs;
- PR #126 run `34758354978` — all nine jobs completed failure with zero steps.

A historical run `34114209121` succeeded, so standard `ubuntu-latest` / `windows-latest` labels are not intrinsically invalid for this repository.

The first PR #126 run also proves repository Actions is not globally disabled: `windows-desktop-smoke` job `103726330788` acquired a Windows hosted runner, completed checkout, Python setup, and dependency installation. Therefore the zero-step failures are a separate **job-level pre-runner scheduling/eligibility failure**. The exact GitHub platform/account reason is not exposed by the available connector surface. Billing, quota, policy, concurrency/account restriction, and transient scheduler behavior remain hypotheses only.

### B. Proven repository workflow defect (fixed)

The one job that acquired a runner provided concrete logs and revealed a stale workflow configuration, distinct from the zero-step failures:

- install command `python -m pip install -e ".[dev,desktop]"` emitted `loom-agent 0.1.0 does not provide the extra 'desktop'`;
- pytest failed before collection with `ERROR: file or directory not found: tests/test_desktop_state.py`;
- repository `pyproject.toml` has optional extra `desktop-agent`, not `desktop`;
- repository scripts contain `loom`, `loom-skill`, `loom-web`, `loom-app-server`, but no `loom-desktop`;
- repository package config contains no `loom_desktop.py` module;
- code search found no current `tests/test_desktop_state.py`.

Because this is direct log/repository evidence, the branch fixes `.github/workflows/ci.yml` at commit `97b50b8d633dcc9df735fd49a20571588bd593af` by:

1. removing stale `loom_desktop.py` from the compile command;
2. removing stale `loom-desktop --help` CLI smoke;
3. removing the obsolete `windows-desktop-smoke` Qt job that referenced removed tests and a nonexistent extra.

No speculative changes were made to the other jobs. In particular the general test job remains `.[dev]`; `test_browser_use_compat.py` uses `pytest.importorskip("browser_use")`, so the evidence reviewed here does not justify globally forcing the browser extra.

### Current CI validation statement

The stale Qt workflow defect is now fixed in the branch. The general parity `test` job has still not reached a runner in the observed runs, so the nine parity tests still have no CI execution result.

**Current statement: `contract committed, CI not executed` for the parity suite.**

## Parity scorecard

| Domain | Rating | Evidence / reason |
| --- | --- | --- |
| Turn/Step | **Partial** | Immutable per-sample step + durable pending state; full Codex activation/managed-authority/serialized-refresh semantics absent. |
| Approval/Sandbox | **Partial** | Strong fail-closed snapshots, durable approval, binding checks and sandbox tests; Codex amendment/reviewer/escalation broader. |
| Exec | **Partial** | Process lifecycle + exact pending-action approval test; stacked typed-action/orchestrator work unvalidated. |
| Patch | **Partial** | Atomic patch/preimage/diff + exact pending patch test; typed patch identity remains draft stack. |
| Context/Compaction | **Partial** | Summary/budget/auto-compaction tests exist; direct-tool metadata compaction Missing. |
| Instructions | **Partial** | AGENTS/skill snapshot/restart coverage; serialized refresh/cancellation authority not proven. |
| MCP | **Partial** | Config/binding/credential safety; prepared-call catalog revision/refresh Missing. |
| Network | **Partial** | Policy/sandbox pieces; Codex amendment semantics and completed OS egress enforcement not demonstrated. |
| App Server | **Partial** | Durable approval protocol + reconnect test; Loom wire/generation model is adapted rather than Codex v2 equivalent. |
| Recovery | **Partial** | Durable restart/torn-log/approval-binding recovery substantial; full Codex rollout/history edges not mapped. |
| Concurrency | **Partial** | Same-session lease + cross-session concurrency regression; full Codex governance concurrency not ported. |
| Tests/CI | **Partial** | 9 contracts committed; one proven stale CI job removed, but parity `test` job still has no executed result because of separate zero-step pre-runner failures. |

No domain is labeled `Codex-equivalent` without end-to-end source and executed-test evidence.

## Blockers

1. **Executed direct tool-call metadata (`#45185`)** — no Loom output-bound metadata ledger, completeness markers, request/pending budgets, or completion/drop/cancel release semantics.
2. **Metadata-aware compaction** — cannot be faithfully ported before direct metadata representation exists.
3. **MCP catalog revision / prepared-call authority** — stale prepared calls must be rejected before preparation side effects while active preparations retain captured authority.
4. **Step activation / managed authority** — immutable snapshot is not the full Codex activation/live-managed-auth/serialized-refresh state machine.
5. **Approval amendment / network authority** — exact Codex network/MCP amendment and reviewer semantics are not represented on reviewed base.
6. **Network enforcement completion** — policy classification is not equivalent to OS-level egress enforcement.
7. **CI job-level pre-runner failure** — exact scheduler/platform cause remains unknown; parity test job does not start.

## Open PR merge guidance

### Absolute hold

- **#121 — `Align runtime execution boundary with Codex orchestration`**: P0 approval/sandbox/step-authority changes. Its own description acknowledges remaining StepContext/MCP/backend-denial/static approval/network gaps. Do not merge until its tests and this parity suite execute.
- **#122 — `Introduce typed exec action identity`**: draft stacked on #121; changes action/approval binding. Do not merge before parent validation and executable tests.
- **#123 — `Add typed apply-patch action identity`**: draft stacked on #122; changes patch approval identity. Do not merge before parent-stack validation and executable tests.
- **#120 — `feat(agent): add Codex-style exec and network policy`**: safety-relevant exec/network policy; its scope says sandbox egress propagation is follow-up. Do not merge it as completed Codex network parity or while the safety validation path is unavailable.

### Outside this window / not certified

- **#119 — `fix(reasoning): align effort controls with Codex semantics`** is primarily reasoning/provider-state work, outside this window's core approval/MCP/exec focus. It is draft and not certified by this report.

## Acceptance summary

- Current Codex baseline re-read and pinned before implementation.
- P0/P1/P2 inventory built from exact Codex test/source files before writing Loom tests.
- 9 high-value cross-module parity contracts committed; 0 production runtime files changed.
- Current direct metadata/metadata-compaction and MCP refresh gaps explicitly remain Missing rather than being replaced with invented tests.
- CI evidence is split correctly: zero-step jobs are not called pytest failures; the one runner-acquired job exposed a real stale YAML defect which was fixed from direct evidence.
- PR #126 remains draft.
- **Validation state: `contract committed, CI not executed` for the parity suite.**
