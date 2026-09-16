# Codex mid-turn continuity closure

Date: 2026-09-16

This change closes the integration gap left between Window01 (turn/Step ownership), Window03 (context compaction), Window05 (app-server resume routing), and Window06 (persistence/recovery).

## Problem

Loom already matched Codex's compacted-history shape: recent genuine user messages plus a contextual-user summary. It also re-injected a fresh `LOOM_RUNTIME_STATE` on each request. What was missing was the lifecycle link between those pieces:

1. auto compaction inside an active turn did not carry a stable reference to the captured Step/world-state baseline into replacement history; and
2. `thread/resume(recoverTurnId=...)` expected `recover_turn_if_idle()` but the runtime did not implement that primitive, leaving safe handoff unable to continue the already-recorded logical turn.

The observable failure mode was repeated re-orientation after compaction or handoff: the model could know the broad task from the summary yet behave as though a new worker had taken over and re-run repository inspection.

## Codex contract followed

Current Codex distinguishes standalone/pre-turn compaction from `CompactionPhase::MidTurn`. Mid-turn compaction injects initial/reference context using the captured `StepContext` and `WorldState`, then replaces history without turning compaction into a new user turn. Separately, safe handoff recovery resumes sampling under the existing `turn_id` with no new user input.

Loom now mirrors those semantics at its provider-neutral boundary.

## Implementation

### Mid-turn compaction reference

Auto compaction while `AgentStatus.RUNNING` locates the Step already captured for the current model request. The compacted window receives one named contextual-user item, `loom_compaction_reference`, placed immediately before the last retained real user item (or before the summary when no real user item remains).

The reference contains only read-only continuity evidence:

- session / turn / step identity;
- state digest;
- workspace and model profile labels;
- permission-description snapshot;
- turn diff revision / changed paths; and
- captured model-step number.

It intentionally does **not** serialize `ToolRouter`, MCP bindings, approval authority, cancellation tokens, process handles, or other process-local execution capability.

The reference is excluded from `is_real_user_message()`, so later compactions do not recursively promote synthetic continuity metadata into genuine user history. The compaction summary remains last. Before committing, Loom rechecks both message count and token budget; if necessary it drops the oldest retained genuine user messages, and if reference+summary still cannot fit it keeps the already-validated replacement without the extra reference.

`CONTEXT_CHECKPOINTED` now records `compaction_phase`, whether the reference was injected, and the same safe reference payload for auditability. The checkpoint's `world_state_digest` is derived from the actual captured Step on the mid-turn path rather than a newly synthesized Step.

### Same-turn safe-handoff recovery

`DurableAgentRuntime.recover_turn_if_idle(session_id, existing_turn_id)` now provides the primitive already expected by `app_server_recovery_contract.py`.

It requires:

- the exact persisted unfinished `turn_id`;
- persisted status `RUNNING`;
- no live runtime owner;
- no pending approval;
- no pending tool calls; and
- no pending step id that could represent an admitted action with unknown outcome.

On success it clears stale binding digests, discards any process-local captured Steps, creates a fresh cancellation/execution stack, and calls the normal turn driver **without appending a user message and without emitting a second TURN_STARTED boundary**. Model/tool limits and the logical `turn_id` remain continuous.

On ambiguous action state it fails closed. That state belongs to Window06 unclean-loss finalization rather than automatic retry.

## Safety invariants

- Compaction continuity metadata is evidence, never execution authority.
- Safe handoff cannot replay persisted approvals.
- An action with unknown outcome is never automatically retried.
- Recovery never fabricates a new user request.
- Recovery never changes the logical turn id.
- Old synthetic compaction references do not survive as real user messages in later compactions.
- Ordinary unclean crash handling remains `recover_interrupted()` and is unchanged.

## Regression coverage

`tests/test_codex_midturn_continuity.py` verifies reference placement, same-turn/step identity, checkpoint digest alignment, summary-last behavior, and recursive-compaction filtering.

`tests/test_same_turn_recovery.py` verifies same-turn completion without new user input, preservation of prior assistant progress, exact turn-id matching, and fail-closed handling of pending execution state.
