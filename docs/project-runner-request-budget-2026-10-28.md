# Project token budget at request boundaries; MXC probe interruption

## Why

`tokenBudget` was checked only between episodes. In the 2026-10-28 real run one
worker episode issued many model requests and reached 128,216 tokens against an
80,000 budget. The qualification script had its own request guard; production
had none.

## What production now does (`app/project_runner/budget.py`)

**One gate for every model call.** The Host wraps the runtime's
`model_executor` with `BudgetedModelExecutor`. Generation, retries the turn
issues, malformed-response resampling and in-turn compaction summaries all call
`model_executor.execute`, so all of them pass admission. Idle compaction and
title inference stay disabled for project sessions; if one were ever called, it
would be refused, because a project session may only send requests inside an
admitted episode. Ordinary chats pass straight through.

**Admission (before a request is sent).** In one `ProjectStore` transaction,
under the store lock that serializes both parallel workers:

* ledger owner = parallel team root, else the run (integration children keep
  their own copied budget, as before);
* `charged = known + in-flight + assumed-unknown`;
* refuse if `charged >= tokenBudget`, or if `charged + largest request seen so far
  > tokenBudget`;
* otherwise record an `open` request row that reserves the largest request seen.

A refusal raises `ModelAdmissionRefused`. The turn runner turns that into
`LIMIT_REACHED` (nothing was sent, so history is whole). Settlement sees the
refused row and marks the run `budget_limited` with its task still pending and a
note in the handoff, not `needs_review`. Explicitly adding budget resumes it.

**After a request.** The row becomes `returned` (with the response's usage),
`rejected` (a provider 4xx other than 408: refused before generation, zero), or
`unknown` (failure or cancellation after sending, or a response without usage).

**Exactly-once usage.** Known tokens are rows in `project_usage`, keyed by the
runtime event that reports them (`MODEL_RESPONSE`, `MODEL_RESPONSE_REJECTED`,
`CONTEXT_CHECKPOINTED.summary_usage`). The same event can arrive from three
places: the live runtime listener, episode settlement (which re-reads the
turn's events) and a restart. `INSERT OR IGNORE` charges it once. An `applied`
flag moves rows into `tokensUsed` once, in the same transaction that sets it. A
response the wrapper saw but whose event was never written (for example,
compaction that failed after a successful sample) is charged at settlement
under its request id. Usage that arrives after its episode settled (a late
response) is applied by the scheduler tick as `late_usage_settled`.

**Restart.** Only the scheduler-lease owner runs `recover()`: open requests from
a previous process become `unknown` and are assumed to have cost the largest
request seen, or their own input estimate if nothing has been seen yet.

**Reported numbers** (`budgetPosition` on `projectRun/get` and `/list`):
`knownTokens`, `inFlightTokens`, `assumedUnknownTokens`, `unknownRequests`,
`refusedRequests`, `largestRequestTokens`, `chargedTokens`,
`enforcement: "request_boundary"`, `hardBillingCap: false`.

## What it is not

Not a provider billing cap. A request that has been admitted runs to completion
and is charged. It can cost more than the largest request seen so far: the
history keeps growing, and the output is not capped. Capping
`max_output_tokens` would truncate reasoning models, so it is left alone.
Unknown usage is an assumption, not a measurement. A hard spending ceiling can
only come from the provider account's own limit.

## Locks

The listener and admission run on runtime worker threads and take only
`ProjectStore.lock` (plus SQLite `BEGIN IMMEDIATE` on its single connection).
The scheduler never waits on the runtime while it holds that lock. A closed or
unreadable ledger refuses (fails closed). A listener error is swallowed by the
runtime, and settlement re-reads the events.

## MXC sandbox: a probe that was killed made the next probe fail

Zero-model diagnosis, 2026-10-28:

* `wxc-exec --probe` took **38 s** and printed `DACL recovery: 2 file(s), 6
  ACE(s) restored`. The next two probes took 103 ms and 56 ms.
* Loom killed the probe after 8 s, and the qualification preflight force-killed
  its launch after 20 s. A probe or launcher killed in the middle of that
  recovery leaves the ACEs behind, so every later probe has to recover them
  again and times out the same way. That fits the earlier 8 s probe timeouts.
* The earlier DACL mutex timeout on the development checkout fits the same
  picture: an inherited `PYTHONPATH` declared the whole repository as a
  read-only DACL path. The harness already drops it. This is an inference from
  the logs, not a reproduced measurement.

Changes: the production probe waits up to 60 s and never kills a running probe
(it fails closed and lets the probe finish). The qualification preflight waits
up to 120 s. With the ACEs restored, the preflight (windows-mxc tier
`appcontainer-dacl`, workspace and network isolation enforced, the runtime
Python launched inside the sandbox) passes in 2.7–3.6 s with no model call.
The sandbox was not disabled or widened.
