# Loom Runtime v2 — durable thread state

This layer keeps long-lived intent separate from ephemeral execution.

## Persistence model

- `session.json` remains the canonical model-visible conversation snapshot.
- `events.jsonl` remains the append-only observable event stream.
- `agent_runtime/state.db` stores durable cross-turn state with SQLite/WAL:
  - one goal per Loom session/thread;
  - queued future turns;
  - transactional queue claim state.

The runtime does **not** persist Python stacks, subprocess handles, or model private reasoning.

## Durable goals

A goal has:

- objective;
- status (`active`, `paused`, `blocked`, `complete`, `budget_limited`, `usage_limited`);
- optional token budget;
- cumulative model-token usage while the goal exists.

`continue_goal()` reconstructs a new Turn from durable history and the stored objective. This is intentionally continuation by state, not resumption of an interrupted Python instruction pointer.

## Durable queue

`enqueue_turn()` can persist work independently of the active Turn. A completed Turn automatically drains queued work (bounded by `max_auto_queued_turns`).

Dispatch protocol:

1. SQLite transaction claims the oldest pending item and binds it to a generated Turn id.
2. The session snapshot adopts that Turn id and persists the queued text as a normal user message.
3. Runtime emits `queue_dispatched` and runs the Turn.
4. Once the input is durably adopted, the queue claim is acknowledged.
5. On restart, a stale claim whose Turn was never adopted is released back to pending; a claim matching the session's persisted current Turn is removed to avoid duplicate delivery.

This means another thread/process can enqueue while a model Turn is busy without rewriting `session.json`.

## Interrupted history repair

When a persisted session says `running` after process restart, `recover_interrupted()` repairs the canonical model transcript before marking the Turn interrupted:

- every assistant tool call without an output receives a structured `aborted` tool result;
- orphan tool outputs are removed;
- duplicate outputs are collapsed to the first output;
- pending tool/approval state is cleared;
- a `history_repaired` event records exactly what changed.

This keeps provider-facing tool call/output history balanced after crashes.

## Deliberate boundaries

This layer still does not claim:

- OS-level sandboxing;
- persisted live terminal processes across Loom restarts;
- automatic infinite goal execution;
- multi-agent graph persistence.

Those remain separate layers so durable intent does not become coupled to process implementation details.
