# Browser request latency: bounded, semantics-preserving optimizations

## Observed bottleneck

The inspected `新版主页` turn (`c89707a2-8cc4-4e85-9d92-7fb1c539515b`)
spent approximately 155 seconds in successful model requests across its first
eight actions, versus 26.6 seconds in tools. A request also waited about 21
seconds without receiving a stream chunk before retrying. Local request
preparation was about 0.18–0.31 seconds per step. The provider reported roughly
554k input tokens on the first successful request, mostly cached.

These observations distinguish model/transport waiting from tool execution.
They do not establish that context size alone caused the delay. Local CPU
optimization cannot eliminate provider first-token latency.

## Changes

- Reuse tool-result request projections by immutable message identity and token
  limit. Content, call IDs, reduction statistics and durable history are unchanged.
  Changed limits recompute the projection; a new message with the same call ID
  cannot retrieve another message's result. LRU limits are 2,048 entries and
  4 million source-message characters (not a promise of 4 MB process memory).
- Collect fresh tab metadata and fresh page DOM concurrently under the existing
  command fence, rather than adding their latency. There is no browser-state
  cache. Protected-page handling, DOM error reporting and ownership locks remain.
- Tell the model to use an action's returned observation, avoid unnecessary
  clicks before indexed typing, and wait for an observed condition rather than
  routinely waiting after every action. Caret placement and click-specific
  behavior remain explicit exceptions. Dependent actions still require the
  current revision; they are not blindly batched.

No screenshots, historical observations, user evidence, permission checks,
navigation guards or action verification were removed. No provider/model or
reasoning setting was silently changed.

## Validation and limits

Context/composer/budget, browser runtime/observation, extension concurrency and
native editing regressions passed, including the opt-in real headless Edge test.
New tests cover warm reuse, budget changes, same-call-ID replacement, Unicode,
collapse equivalence, bounded eviction, concurrent fresh reads and both read
failure paths. User browser sessions were not driven.

A synthetic comparison with the pre-change reducer used 265 immutable tool
messages, 900 repetitions of `evidence-<index> ` per message, a 512-token output
limit and 30 runs. Median projection time was **9.518 ms before / 0.261 ms warm**;
projected messages and statistics matched exactly. This measures only the
reducer, not end-to-end task latency. The concurrency test uses a deterministic
gate to prove overlap, not a timing-sensitive speed assertion.

Model obedience to the shorter action guidance and end-to-end latency on real
websites still require runtime observation after deployment. This is not a
claim of universal optimality, a complete browser acceptance run, or elimination
of model thinking between dependent actions.
