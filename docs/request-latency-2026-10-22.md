# Request preparation and latency measurement — 2026-10-22

Baseline source: `a5e5663b572ba869ce2861c412d9f2a008c629f8`.
Scope: request projection and event-based measurement. No model, reasoning,
permission, tool execution policy, release pipeline or renderer changes.

## Evidence and bottleneck

Reviewed browser-latency-20261021, context-continuity-review-2026-10-07,
memory-v3, provider-streaming, long-conversation-performance (October 8/19),
performance-review and performance-audit (October 8), then checked the current
composer, reducer, estimator, runner and executor.

The earlier browser report records ~155 seconds of successful model requests,
26.6 seconds of tools and 0.18–0.31 seconds of preparation per step. Its 554k
provider input tokens were mostly cached; this is not causal evidence that
context size was the main latency problem. Its referenced session is no longer
present in this machine's default session directory, so those figures remain
attributed to that report, not independently remeasured here.

Read-only analysis of available session `223b796b-ccd9-41ed-9791-e3154d804b31`
provides additional observations (timestamps, no prompts/results exported):

| Turn | Configuration (provider / model; profile agent.fast) | Wall | Closed model attempts | Closed model spans | Known preparation |
| --- | --- | ---: | ---: | ---: | ---: |
| e74b051e-1377-4d53-a97a-91fde24eba62 | loom-primary / step-5-preview-free | 8.388 s | 3/3 | 6.390 s | 1.139 s, 1/3 requests |
| 4f57dabe-928d-4e03-b344-65149bb66cfe | loom-primary / longcat-2.5-preview-free | 1210.214 s | 6/7 | 441.890 s | 1.354 s, 7/7 requests |
| afe5ddd7-8c52-4e3a-b102-136f3c335da2 | loom-primary / minimax-m3.1-flash | 2074.648 s | 69/70 | 1612.445 s | 9.756 s, 48/70 requests |

The first turn failed; the other two were cancelled. All have null reasoning
and output-cap settings and full-access permission in the recorded snapshots.
They are different tasks/models, **not paired before/after comparisons**.
The last turn has 360.433 seconds of per-call tool spans (65 starts); these may
overlap and must not be treated as additive wall time. Missing/unclosed attempts,
legacy duplicate preparation and uninstrumented gaps remain unknown. None of
these three turns has recorded quality checks. The evidence supports prioritizing
model/transport latency investigation over local CPU; it does not establish
whether provider queueing, reasoning, network or context processing is dominant.

One concrete local defect: `render_request` cloned every string-content assistant
message, even when stripping legacy controls did nothing. This invalidated the
existing identity-keyed estimator cache on every preparation/budget render.
Only messages whose content actually changes are now replaced. Exact content,
reasoning, tool IDs/arguments, roles, provenance, frames, observations and durable
history remain identical. No new cache or retained-memory budget is introduced.
Unfinished tool calls still bypass the estimator memo as before.

The reducer's repeated whole-request estimation looked expensive, but its optional
target-collapse path has no current production caller. It was excluded from this
change. Browser observation concurrency and tool projection caching already exist;
they were not implemented again or credited to this task.

## Measurement contract

`scripts/latency_report.py EVENTS_JSONL [--turn-id ID]` reads existing events and
prints content-free JSON. It retains event/step/call IDs and immutable request
configuration, and never drives a session or contacts a provider.

- Preparation: `MODEL_REQUESTED.request_preparation_ms`; reused transport requests
  now record zero plus `request_preparation_reused=true`. Older repeated step IDs
  with no reuse flag are ambiguous, not summed again.
- First response: `stream_timing.first_chunk_ms` is the first envelope, possibly
  role-only. `first_content_ms` is the first substantive text/reasoning/tool fragment,
  **not** public-text TTFT. No hidden reasoning text is added to telemetry.
- Generation interval: new `stream_timing.execution_ms` minus first substantive
  time uses the same monotonic clock origin. It includes subsequent stalls,
  stream termination and internal transport retries, not just active compute.
  Old records without a compatible total retain null generation duration.
- Executor admission wait: new `queue_wait_ms`, before the stream clock starts.
  An admission failure before a control exists has no measured split.
- Tools: `TOOL_STARTED` to completed/failed by call ID; requested-to-start is
  permission/queue delay, not execution. Parallel calls stay separate.
- Retries: each outer request event is a distinct attempt, including repeated
  step IDs. Existing transport retry events annotate internal retries and must
  not be added again to model execution duration. Runtime backoff is measured
  monotonically and attached to the next request as `retry_wait_ms`. A cancellation
  before another request leaves that final wait unmeasured, not zero.
- Terminal wall time and check verdicts are reported separately. Completion is
  not proof of task quality; a missing check is not a pass. Event-timestamp fallback
  spans include persistence overhead. Incomplete/malformed JSONL fails explicitly.

The stream clock does not measure backend request serialization separately from
provider/network first-response wait. Preparation can include compaction work;
these spans are diagnostic, not an additive critical-path or billing ledger.

Token fields are deliberately separate:

| Field | Meaning |
| --- | --- |
| estimated_input_tokens | Local byte/protocol/image estimate; not a provider tokenizer or bill |
| calibrated_input_tokens | Local estimate adjusted by historical, model-attributed samples |
| provider_usage | Usage reported through response metadata; missing values stay null |
| cached_input_tokens | Provider-reported subset of input, not additional tokens; not necessarily free |
| actual_cost | Null: invoice amount cannot be derived from counts without provider-specific pricing, discounts and billing evidence |

Legacy `ModelUsage()` can serialize all-zero values when usage was unavailable;
the report warns about this ambiguity rather than asserting zero consumption.
Failed requests may consume unreported tokens. Summed input across steps counts
repeated context multiple times and is not unique context size. No pricing or
token savings are claimed: the optimization preserves the request bytes.

## Paired validation

`scripts/benchmark_request_projection.py --baseline-ref a5e5663b572ba869ce2861c412d9f2a008c629f8 --runtime-runs 5`
loads that trusted Git revision's composer without checking out or modifying
other work. It alternates before/after order, warms the same existing estimator
cache before each of 21 samples, and compares exact message values and estimates.

| Measurement | Before median | After median | Result/quality |
| --- | ---: | ---: | --- |
| Synthetic history projection + estimate, 240 messages | 9.104 ms | 0.946 ms | Exact messages and estimate equal in all 21 pairs |
| Real history-only projection replay, 170 messages | 1.086 ms | 0.246 ms | Exact messages and estimate equal in all 21 pairs |
| Deterministic runtime file-read task, five runs each | 386.218 ms | 419.890 ms | 5/5 success both; exact answer LOOM-42; one verified read, two model calls each |

The synthetic estimate is 1,537,989 tokens: deliberately a large CPU fixture, not
a request sent to a provider. Its output SHA256 is
`0719fde588ccf6d83af0cef064512197aec8e7cc5a284bfa52f25eac19311b72`.
The real history-only replay estimate is 109,653, output SHA256
`234faf888ddbf9ba9e1353579ca236b93413b7ac36a7966ae3670d7086c931a8`.
Replay uses a single in-memory snapshot of the session's canonical messages;
it does not reconstruct live frames, tools or provider cache state. Reproduce
with `--session PATH_TO_SESSION_JSON`; raw private content is never printed.

The runtime task uses the same scripted model, agent.fast profile, read-only
permission, sandbox OFF and isolated temporary file in both variants. Real
runtime preparation, tool execution and durable event writes run. It validates
the tool-result correlation and exact answer; it does not test model reasoning.
Only the composer is swapped, isolating this optimization. The small runtime
fixture shows **no end-to-end improvement** (after is slower); local CPU savings
do not establish task-level speedup over filesystem/scheduling noise.

99 focused tests passed covering context composition/frame repair/growth,
reconstruction, calibration, compaction continuity, provider streaming, executor
deadlines, retry behavior, permissions, execution bindings and memory. Added
deterministic tests enforce unchanged-message identity reuse, changed legacy
content, unchanged tool association/reasoning, retry preparation reuse, separate
stream clocks, parallel spans and missing usage/timing behavior.

Tests used bundled Python 3.12 with isolated dependencies in the ignored
`.loom/tmp/latency-test-deps` directory (`openai` 2.54.0, within project bounds).
No dependency lock, configured credential or runtime installation changed.

## Limits and next measurement

No live provider requests or external task side effects were replayed. The original
external task environment is not isolated/reproducible here, and no matching
before/after provider-cache state or quality oracle is available. Credentials
were neither read nor exported. Real end-to-end latency, success rate and answer
quality improvement therefore remain **unverified**. Do not use these local
measurements as a production latency promise.

For a subsequent live comparison, keep task/environment snapshot, provider,
model, reasoning, output cap, permission, tool set and runtime version fixed;
retain both raw event logs privately, alternate runs, identify cache state,
record independent acceptance checks, and compare both failed/cancelled and
successful runs. Use the new report for each turn; do not aggregate across the
three differing model configurations above or discard failures from the sample.
