# Codex persistence / recovery / session parity v1

## Baselines

- Codex source baseline: `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1` (`main` HEAD re-read at task start, 2026-09-13).
- Loom implementation baseline: `yuchenm1303-png/Loom@b3dc3db50b117615be59b79806a6fd8fd38b02a8`.
- Loom branch: `codex-recovery-session-parity-v1`.

The port target is observable recovery semantics, not Rust implementation structure. Loom-only durable goals and queued turns remain a product-layer feature and do not become permission to resume a Core turn after process loss.

## Codex source mapping

| Codex source | Contract / state machine / tests | Loom counterpart | Gap found | Port decision |
| --- | --- | --- | --- | --- |
| `codex-rs/core/src/session/session.rs` | `Session` owns persisted-session facilities separately from `active_turn: Mutex<Option<ActiveTurn>>` and the input queue. | `AgentSession` plus `DurableAgentRuntime` | Loom serialized execution-shaped fields in `session.json` and could mistake them for resumable capability. | Treat persisted pending execution fields as audit/reconstruction evidence only after process loss. |
| `codex-rs/core/src/state/turn.rs` | `ActiveTurn`, `RunningTask`, cancellation/task handles, pending approval oneshot senders, pending permission/user-input state are process-local. | Runtime active token, `pending_tool_calls`, `pending_bindings`, `pending_approval` | Loom snapshot outlived the process-local authority that made these fields meaningful. | On restart recovery, invalidate tool/step/binding/approval capability and mark turn interrupted. |
| `codex-rs/core/src/session/input_queue.rs` | `InputQueue` is an in-memory mailbox. | Steering plus Loom durable product queue | Loom has intentionally durable goals/queued turns. | Keep Loom product queue durable, but never interpret it as continuation of the crashed Core turn. Consume turn-local steering when interrupting. |
| `codex-rs/core/src/session/rollout_reconstruction.rs` | Reconstructs model history, turn settings, retained context, world/reference context and compaction state from persisted rollout segments. `TurnComplete` / `TurnAborted` delimit replay; surviving compaction can replace earlier history. | `session.json`, event log, context checkpoints | Loom had snapshot-oriented recovery rather than an explicit durable-history-versus-execution boundary. | Preserve canonical durable transcript and make crash recovery terminal/idempotent. Context projection remains separate. |
| `codex-rs/history/src/lib.rs` | `RolloutItem` is the persisted domain. `ResponseItemEnvelope` preserves response items plus harness metadata. `CompactedItem.replacement_history` is a recovery checkpoint and stores latest reachable token usage. | `AIMessage` history + Loom context checkpoint events/state | Loom compaction format is product-specific. | Preserve Loom checkpoint representation; require only committed checkpoints to influence reconstruction. |
| `codex-rs/rollout/src/recorder.rs` | JSONL rollout persistence; resume loads valid durable records; persistence queues retry after materialization failure. Loader reports parse errors. | `events.jsonl` + `.pending-commit.json` redo journal + atomic `session.json` | Different persistence mechanism, same durability goal. | Keep Loom atomic snapshot/redo design. Torn final event is repairable; malformed interior event remains fail-closed because Loom's writer invariant makes it corruption, not an expected crash boundary. |
| `codex-rs/core/src/context_manager/normalize.rs` | Missing call outputs are synthesized as `aborted` only for in-memory model prompt normalization; synthetic repair is not persisted. Stable derived IDs avoid cache churn on repeated resume. | `repair_tool_history()` | Loom recovery previously wrote repaired messages back to durable `session.json`. | Stop persisting recovery repair. Record projection counts only. The model-request layer must apply the projection ephemerally before future sampling. |
| `codex-rs/core/src/session/handlers.rs` | Shutdown/abort interrupts tasks and terminates process-local execution resources; approval responses target live in-memory waiters. | Runtime cancellation/process manager/approval resume | Persisted approval or process identity cannot recreate its old waiter/handle after restart. | Never auto-continue approved/pre-exec, running process, or pending tool state after process loss. |
| `codex-rs/app-server/tests/suite/v2/thread_resume.rs` | Same-server resume can rejoin a running thread. Resume tests also replay pending command/file-change approval requests. A rollout tail without a terminal turn is presented as interrupted. | Loom `thread_resume`, `recover_interrupted()` | Loom app-server currently only loads snapshot on resume; it does not distinguish live rejoin from restart recovery. Loom also cannot yet safely mint a fresh approval binding on replay. | Provide an idempotent recovery primitive now. Window05 must call it only when there is no live turn. Approval replay needs a fresh-binding interface from window02; until then restart recovery interrupts rather than reusing stale authority. |
| `codex-rs/app-server/tests/suite/v2/thread_fork.rs` | Fork consumes persisted history and creates a new thread identity; tests preserve intended persisted history/configuration semantics. | Loom fork/session cloning | Fork must copy history/product state intentionally, never live execution authority. | No pending approval/tool/process binding may be inherited as an executable capability. |

## Codex durability boundary

1. **Durable history** is the persisted rollout domain: response items plus protocol/session/turn/context/compaction events required for reconstruction.
2. **Model context reconstruction** replays the surviving rollout segment, using the newest surviving compaction replacement history as a checkpoint and replaying the suffix.
3. **Active turn execution is not serialized**. Tokio task handles, cancellation tokens, approval waiters, live process handles and input mailbox contents are process-local.
4. **Interrupted turn representation** comes from a started/non-terminal tail or an explicit abort/interruption boundary; persisted work remains visible, but the old execution stack is not revived.
5. **Pending tool execution is not a restart capability**. A missing tool output can be represented to the next model request by a synthetic aborted output, but that synthetic repair is not written into the rollout.
6. **Pending approval** is special: app-server resume can replay an approval request, but it does not serialize and reuse the old oneshot waiter. Loom therefore needs a fresh authority/binding for equivalent behavior.
7. **OS process execution** is process-local. After process loss, ownership/completion cannot be proven from the old handle; recovery must not reattach or re-execute automatically.
8. **Input queue** is process-local in Codex. Loom's durable goals/queue are product features and remain isolated from Core turn continuation.
9. **Thread resume != turn resume**. Resume reconstructs/reattaches a thread. Same-process rejoin can observe a still-live active turn; process restart cannot resume the old task stack.
10. **Fork/rejoin**: fork creates a new thread from persisted history; rejoin attaches to an existing live thread when one exists.
11. **Item/call IDs** already persisted in response items survive reconstruction. Synthetic normalization IDs are deterministic so repeated resume does not churn prompt identity.
12. **Compaction checkpoint** participates only once its compacted record is durable. An incomplete compaction cannot replace earlier canonical history.

## Recovery matrix

| State | Codex behavior | Loom before this branch | Target / this branch |
| --- | --- | --- | --- |
| Idle thread | Resume reconstructed persisted history; no active task. | Loads snapshot. | Same. No recovery mutation. |
| Active sampling | Same-process rejoin may observe live turn; after process loss, no sampling task is serialized and non-terminal tail is interrupted. | Persisted `RUNNING` could remain apparently active after restart. | `recover_interrupted()` marks `INTERRUPTED`; no sampling continuation. Window05 must invoke only when no live turn exists. |
| Waiting approval | Resume can replay a new approval request; old in-process waiter is not the durable authority. | `WAITING_APPROVAL`, approval and binding survived snapshot. | Old approval/binding/tool state is invalidated and turn is interrupted. Future parity requires window02 to mint a fresh replay binding. |
| Approval accepted / pre-exec | No process-local task continuation is provable after restart; must not infer that execution should start. | Pending tool state could survive. | Interrupt; clear pending calls/bindings. Never auto-execute. |
| Tool running | Live process/tool handle is process-local; restart cannot prove ownership/completion. | Event/UI could continue to look running. | Core recovery interrupts and never reattaches/retries. Window05 should render unfinished process/tool item as interrupted/unknown under an interrupted turn. |
| Tool completed / pre-observation | Side effect may have happened, but without durable tool output the durable history cannot prove result. Prompt normalization can synthesize `aborted`/unknown. | Recovery wrote a synthetic aborted observation into durable messages. | Durable messages remain unchanged. Recovery records projection counts with `persisted=false`; do not retry automatically. |
| Compaction running | Only a durable compacted checkpoint replaces history. | Checkpoint behavior is Loom-specific. | Ignore incomplete/uncommitted checkpoint; reconstruct from last durable state. |
| Cancelled turn | Terminal cancellation/abort is durable; no continuation. | Core runtime already persists cancellation and currently repairs history durably. | No restart continuation. Durable-repair-on-cancel is owned by window01 and should be compared with Codex prompt-only normalization separately. |
| Crashed turn | Persisted history remains; non-terminal active turn is interrupted. | `RUNNING` crash recovery persisted synthetic repair and did not clear bindings. | Interrupt idempotently, preserve durable transcript, clear execution capabilities. |
| Reconnect without process restart | Rejoin existing live thread/turn. | App-server can reconnect to its live runtime. | Do not call crash recovery when a live turn exists. |
| Process restart | Reconstruct persisted thread; no old task/waiter/process continuation. | Snapshot could expose stale active/pending state. | Window05 calls fail-closed recovery when persisted status is active but no live turn exists. |

## Crash windows and fail-closed rules

### Approval accepted, execution not yet started

The durable record can say the user approved, but a restarted process cannot prove whether the old execution task had already crossed into side effects. Recovery therefore does **not** start the tool. The old binding is invalidated.

### Tool running when the process dies

A stale process ID or an old process record is not proof of ownership. Recovery does not reattach, kill by guessed PID, or execute again. The action outcome is unknown unless a durable completion observation exists.

### Tool completed, observation not durable

The side effect may have happened. Retrying would risk duplication. The durable transcript is left unchanged; the next model prompt may receive a synthetic aborted/unknown output as a non-persisted projection so the model must inspect state before retrying.

### Event durable, snapshot not durable (or vice versa)

Loom's `.pending-commit.json` redo record intentionally differs from Codex JSONL-only rollout mechanics. Recovery completes the pair and deduplicates by `event_id`. A torn final JSONL record is truncated before the next append. A malformed interior record remains an error: with Loom's atomic append/redo protocol it indicates corruption rather than an ordinary crash boundary, so fail-closed is safer than silently skipping it.

## Loom-only durability isolation

`DurableThreadStateStore` goals and queued turns are product-layer intent. They survive restart by design. They are not an `ActiveTurn`, tool continuation, approval token, process lease, or input mailbox.

Crash recovery therefore:

- reconciles queue claims against the durable turn id;
- preserves queued turns and durable goals;
- consumes turn-local steering for the dead turn;
- clears pending tool calls, pending step id, pending bindings and pending approval;
- leaves canonical messages untouched;
- emits one durable `TURN_INTERRUPTED` event, and is idempotent thereafter.

## Tests translated / added

- crash recovery preserves canonical durable history;
- missing tool output is counted as a non-persisted model-history projection;
- stale pending bindings are invalidated;
- waiting approval fails closed after process loss;
- durable product queue survives Core turn interruption;
- repeated recovery is idempotent (one terminal interruption event);
- torn final event log record is repaired before next append;
- redo recovery does not duplicate an already-appended event id;
- malformed interior event log corruption fails closed.

Existing Loom tests continue to cover durable goal/queue restart behavior. Codex source/tests additionally establish resume/rejoin/fork, interrupted-tail and compaction reconstruction semantics; app-server/context integration items below are intentionally not implemented from this window.

## Required minimal interfaces from other windows

### Window01 — runtime/turn

Provide a model-request boundary that can accept a **non-persisted history projection** (the existing `repair_tool_history()` output is sufficient as input) so missing tool outputs are synthesized for provider validity without writing them to canonical session history. Also review cancellation/binding-failure paths in core runtime that currently persist `repair_tool_history()` output; this window did not modify those owned files.

### Window02 — approval/sandbox

Provide `replay_pending_approval(persisted_request) -> fresh_process_local_binding` (name illustrative) or equivalent. The fresh binding must be generated by the new process and must not trust the serialized old binding/HMAC. Until this exists, recovery intentionally interrupts a restarted `WAITING_APPROVAL` turn instead of pretending Codex-compatible approval replay is safe.

### Window05 — app-server/UI

On `thread/resume` (and any startup restoration path), distinguish:

- a thread with a live runtime turn in the current process: **rejoin; do not recover**;
- a persisted `RUNNING` / `WAITING_APPROVAL` thread with no live turn: call `runtime.recover_interrupted(thread_id)` once before returning the reconstructed thread.

When rendering events, any tool/process item still marked running inside a terminal `INTERRUPTED` turn should be presented as interrupted / outcome unknown rather than live. Do not fabricate `PROCESS_EXITED` or `TOOL_COMPLETED` events.

## Known intentional deviation from current Codex

Current Codex app-server tests replay pending command-execution and file-change approval requests on resume. Loom cannot yet do this safely because the approval authority/binding regeneration contract is owned by window02 and is not present in this branch. Reusing the serialized binding would weaken the security boundary, so this branch chooses the temporary safer behavior: invalidate and interrupt.

The observable difference is that a user must start/continue a fresh turn instead of approving the old request after a process restart. Once window02 exposes fresh replay binding generation, this deviation can be closed without changing the durability rule established here.
