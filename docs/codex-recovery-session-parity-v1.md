# Codex persistence / recovery / session parity v1 — final

## Frozen baselines

- Final Codex audit baseline: `openai/codex@36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.
- Previous audit baseline: `1715e55076737158ba61d43158ede504de6d4ce1`.
- The one upstream commit between them is Windows Desktop uninstall / ACL plumbing and does not alter the recovery contract below.
- Loom implementation baseline: `yuchenm1303-png/Loom@b3dc3db50b117615be59b79806a6fd8fd38b02a8`.
- Window branch: `codex-recovery-session-parity-v1`.

## Corrected canonical contract

The first revision correctly separated durable history from ephemeral runtime state, but it was too broad when it treated every process restart as a terminal interruption.

Current Codex has an explicit `RecoverTurnRequest` and `CodexThread::recover_turn_if_idle()`. A safely suspended unfinished turn can be reconstructed on a replacement runtime and resume sampling under the **same already-recorded turn id**, with **no new user input**. The old runtime task stack is not revived; a fresh execution stack is created for the existing logical turn.

The canonical state machine is therefore three-way:

1. **Live rejoin** — the current process still owns the live turn. Rejoin it; do not run crash recovery.
2. **Safe handoff recovery** — the old runtime deliberately suspended the unfinished turn, flushed persistence, and closed its writer without recording a terminal turn boundary. A replacement runtime may resume sampling for the same logical turn id. Do not mark it interrupted first.
3. **Unclean process loss** — there is no live owner and safe handoff cannot be established. Fail closed: invalidate stale execution-shaped state, preserve canonical durable history, expose ambiguous work as interrupted / outcome unknown, and never infer that an unfinished action should run again.

This distinction is the final Window06 ruling: **execution-stack recovery is not the same thing as logical-turn recovery**.

## Source → contract mapping

| Codex source | Canonical contract | Loom decision |
| --- | --- | --- |
| `codex-rs/protocol/src/turn_input.rs` | `RecoverTurnRequest` restarts sampling for an interrupted regular turn using the already-recorded `turn_id`. | Window01 must expose the Python-equivalent same-turn recovery primitive. |
| `codex-rs/core/src/codex_thread.rs` | `recover_turn_if_idle()` resumes only when idle and adds no new user input. `suspend_turn_and_shutdown()` leaves the unfinished turn non-terminal so another runtime can recover it. | Window05 must route trusted handoff to Window01 recovery and must not call unclean finalization first. |
| `codex-rs/core/tests/suite/abort_tasks.rs` | Safe handoff test: suspend, verify no `TurnAborted` / `TurnComplete`, resume replacement runtime, recover the original `turn_id`, then complete it. | This is the required integration acceptance shape for Window01/05. |
| `codex-rs/core/src/context_manager/normalize.rs` | Missing tool output may be synthesized as `aborted` for model-context normalization. | Keep this projection non-persisted; do not rewrite canonical transcript during crash recovery. |
| `codex-rs/app-server/tests/suite/v2/thread_resume.rs` | Resume can rejoin live state and can replay pending approval requests. | Window05 owns rejoin/routing; Window02 must regenerate fresh approval runtime authority on replay. |

## Recovery matrix

| Persisted / live state | Final behavior |
| --- | --- |
| idle thread | Reconstruct durable history; no recovery mutation. |
| live active turn in current process | Rejoin live turn. |
| safely suspended unfinished turn | Preserve non-terminal state; recover same logical turn through Window01, same `turn_id`, no duplicate user input. |
| active snapshot after unclean process loss | Mark interrupted and invalidate stale execution-shaped fields. |
| waiting approval, same process | Use the live approval path. |
| waiting approval after restart | Do not reuse old runtime authority. Until Window02 supports fresh replay, fail closed. |
| action may have completed but observation is not durable | Outcome unknown; do not automatically retry. |
| missing tool output in model history | Add only a non-persisted `aborted` projection at model-request time. |
| incomplete compaction | Ignore as replacement checkpoint until durable. |
| Loom durable goal / future queue | Preserve as product intent; it is not authority to revive the lost Core execution stack. |

## Window06-owned implementation

`DurableAgentRuntime.recover_interrupted()` is retained as the current compatibility entry point for **unclean process-loss finalization only**. It must not be treated as the Codex `recover_turn_if_idle()` operation.

For an unclean loss it correctly:

- handles persisted `RUNNING` and `WAITING_APPROVAL` states;
- clears pending tool calls, step id, bindings and approval state;
- preserves canonical durable messages;
- records missing/orphan/duplicate tool-output repair counts as a non-persisted projection;
- preserves Loom durable goals / future queue state;
- emits one idempotent `TURN_INTERRUPTED` boundary;
- does not automatically repeat ambiguous work.

The method name is legacy. Its semantics are now explicitly constrained by this contract. Renaming it is optional cleanup, not a reason to rewrite the production file in Window06.

## Storage contract

Loom's `.pending-commit.json` redo plus atomic snapshot design is a platform-equivalent persistence mechanism, not a Rust rollout clone.

- torn final JSONL record: repairable before the next append;
- redo event already present: deduplicate by stable event id;
- malformed interior record: fail closed under Loom's writer invariant;
- only durable context checkpoints participate in reconstruction.

## Cross-window interfaces

### Window01 — runtime / turn

Provide:

- non-persisted history projection at the model-request boundary;
- `recover_turn_if_idle(existing_turn_id, ...)` or equivalent: same logical turn id, no new user input, fresh StepContext/execution stack, only for a caller-authorized safe handoff.

Also review cancellation / binding-failure paths that still persist `repair_tool_history()` output.

### Window02 — approval / sandbox

Provide fresh approval replay from persisted request data. A restart must not rely on the old process-local approval binding.

### Window05 — app-server / UI

Use three-way resume routing:

1. live turn exists locally → rejoin;
2. trusted safe handoff is recoverable → Window01 same-turn recovery;
3. no live owner and no safe handoff proof → Window06 unclean finalization.

For unclean interruption, UI should show unfinished tool/process work as interrupted / outcome unknown rather than fabricating completion.

## Intentional integration gaps

- Pending approval replay remains blocked on Window02 fresh authority generation.
- Same-turn safe-handoff sampling recovery remains blocked on the Window01 runtime primitive plus Window05 routing.

These are explicit integration dependencies. Window06 must not reimplement Window01/02/05 internals.

## Seal

Window06 is **sealed** for its owned domain after this correction: persistence boundary, reconstruction contract, unclean crash finalization, durable-history integrity, storage crash tolerance, and the three-way recovery state machine are fixed.

The previous blanket statement `process restart => terminal interrupted turn` is superseded by this document. The final rule is:

> Live turn → rejoin. Safe suspended turn → rebuild a fresh execution stack for the same logical turn. Unclean loss → fail closed and interrupt.
