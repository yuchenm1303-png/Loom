# Tool call recovery

Applies to Chat Completions, Responses and Messages, streamed or whole.

## Faults answer their calls

- An undeclared tool name or a schema-invalid argument is a fault of individual
  calls. The response boundary raises `AIToolContractError`, an `AIResponseError`
  subclass that carries the decoded response. The runtime commits the calls and
  never spends a malformed-response retry on them.
- `TurnRunner` plans the batch with `ToolOrchestrator.prepare`, the authority that
  dispatch already uses. If any call is faulty, no call in that batch runs. Each
  call gets a correlated result with `execution_status: not_executed`: the faulty
  call carries its diagnostic (`invalid_tool_request` or `tool_not_in_step`), the
  others carry `batch_not_executed`. Re-issuing the batch cannot repeat a side
  effect, because none ran.
- A call to a registered deferred tool that this step did not declare activates
  that tool for the rest of the turn; the next step declares it. A disabled
  capability is not activated.
- A byte-identical repeat of a call inside one batch runs once and is answered
  `duplicate_call`.
- Five consecutive faulty batches end the turn as a failure
  (`_MAX_CONSECUTIVE_TOOL_FAULT_BATCHES`). `max_model_steps` defaults to unlimited,
  so the bound lives here.

## Streams

- A tool name may arrive whole, split, or as growing snapshots. It resolves against
  the names declared for the request, so `browser` then `browser_eval` yields
  `browser_eval`, not `browserbrowser_eval`.
- An argument object repeated exactly is read once. Any other repetition is
  invalid JSON.
- A call ID reused at another index stays a protocol violation.

## Wire identifiers

- Tool names and call IDs are mapped separately, per request, deterministically.
  Unsafe call IDs (for example `functions.read:0`, or longer than 64 characters)
  reach the provider as `loom_call_<digest>` and decode back on the way in.
- The three vendor declarations come from `app/ai/tool_declarations.py`.

## History replay

- Outputs pair with calls by position inside their assistant message. A vendor
  that restarts IDs per response (`call_0` in every turn) keeps each turn's output.
- Each output follows its assistant message directly. Repaired history, used by
  compaction and projection, lists outputs in call order. A call with no durable
  output gets an unknown-outcome output. Orphan outputs and second answers are
  dropped.
- The Messages transport puts all results of one assistant turn in one user turn.

## Unchanged

- Deferred activations stay turn-scoped (`tests/test_tool_search_runtime.py` pins
  this). A later turn that needs a tool searches again, or takes the recovery
  path above.
- Malformed JSON, duplicate call IDs in one response, mixed structured and textual
  calls, truncation and unrecognised wire formats still take the bounded
  format-repair path.
- Batches stay atomic, as in `docs/tool-call-contract-2026-10-14.md`.

## Defects this reproduced on the previous code

- Three undeclared-tool responses ended the turn: the boundary rejection shared the
  malformed-response budget (`model_retries`, default 2).
- A vendor that restarts call IDs per response lost the second turn's output, so
  the next request had an unanswered call.
- A snapshot tool name doubled (`browserbrowser_eval`); a call ID re-announced at
  another index and a replayed argument object each failed the stream.
- A valid call repeated in one batch ran twice.
- Unsafe call IDs went to the provider unchanged.
