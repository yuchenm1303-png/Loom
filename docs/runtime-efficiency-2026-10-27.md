# Runtime efficiency and recovery contracts

The alignment incident took 2,484.872 seconds, including 42 model requests and five rejected responses. Event replay attributes roughly 2,167 seconds to model requests. This change targets unnecessary requests and oversized repeated input, not weaker verification or an artificial promise about provider response times.

## Context compilation

AgentLimits now defaults to a 64,000-token working context. This is an operating policy, independent of declared provider capacity. Explicit environment/profile settings override it; embedders can set working_context_tokens=None to retain unbounded operating policy. Unknown model capacity stays unknown. Large fixed prefixes make the working target unattainable rather than manufacturing a provider overflow.

The existing request compiler first reduces historical observations to fit that working target while retaining call/result IDs and the two newest observations. Canonical history and durable evidence are not overwritten. Old results contain recovery pointers instead of requiring source tools to run again. Aggregate projection uses one full estimate and incremental accounting, avoiding a history-sized estimate for each observation. If historical decisions/user context still require compaction, use the existing semantic checkpoint machinery. Summary input retains its provider-capacity budget: a working target must not silently erase an indivisible source or force repeated failed summaries.

## Tool discovery and execution

Locally registered deferred tools and MCP tools from the **captured** McpBinding can be resolved into a derived dispatch Step without a new model request. The derived step gets a new ID and preserves original permissions, environment, request state and MCP binding. Hidden, code-only, deselected and disabled capabilities cannot be promoted. Live MCP registry projections cannot replace captured handlers. Arguments and permissions still cross the orchestrator, including approval and restart-binding validation.

Batch faults receive individual correlated outcomes. A valid prefix and later independent reads can execute; an invalid predecessor holds later state-changing actions. Identical read-only calls within a batch remain deduplicated. The model must not replay successful members when correcting a failed member. Native malformed response/tool protocol still fails before dispatch.

Serial, parallel, sandbox and nested execution obtain their services from the same _tool_context factory. Nested calls also cross orchestrator execution. Browser verification therefore receives execution_step, feedback, source tracking and permission snapshots on every route. Sandbox execution records the same fingerprints/effects as other routes.

## Recovery ownership

Sampling transport recovery now consumes the configured transport_retries budget (default two retries) and a 90-second recovery budget. Failed-request elapsed time and Retry-After/backoff count against the budget. A delay beyond the remaining budget fails immediately rather than sleeping early or ignoring Retry-After. Semantic repair has a separate model_retries allowance. Compaction transport recovery uses the same bounds. Steering and cancellation remain interruptible; tools already executed are never replayed as part of sampling recovery.

BrowserOperationError carries an operation ID, resource, execution status and reconciliation policy. Extension operations which were dispatched are retained after timeout, so late receipts are not discarded. New input for that resource is fenced. After settlement, a state observation can reconcile the resource before new input. Queued, undispatched operations are still removed to prevent ghost clicks. The browser-use async adapter likewise waits for cancellation settlement and requires an explicit observation before accepting further input.

Two unresolved-operation failures without browser progress stop the turn deterministically; three identical schema failures do likewise. Three browser-state observations explicitly classified as unchanged also stop no-progress polling; actual state changes, actions and delivered visual observations allow progress. Neither a different explanation, another model request nor different polling arguments reset the underlying operation identity. Extension 0.1.27 sends separate unknown/settled receipts, including late success/failure; older extensions lacking settlement support produce an explicit reload requirement instead of repeated blind retries. This prevents hours of model-driven sleep/poll/retry loops. It does not claim that a timed-out external mutation was cancelled or safe to replay.

Finite managed processes can be awaited to their existing lifetime deadline in one exec_wait call when no shorter wait window was requested. Unbounded/background services retain a finite default wait window; explicit zero-time observation remains available. Process waiting uses output/exit activity events with bounded cancellation checks instead of a 20ms busy polling cycle. No model is needed to decide repeatedly whether a known finite job is still running.

## Verification scope and evidence

Whole-workspace status/HEAD changes no longer create task obligations at tool return or final-answer time. Native file mutations use the turn diff tracker. Shell mutations are attributed at their execution boundary using before/after source fingerprints and commits produced inside that boundary; local mutating execution shares a workspace lease. Scratch/cache directories are excluded before Git enumeration. Byte-identical commits alone do not create an edit obligation. Previously tracked subjects still become stale if their source bytes change.

The lease coordinates this runtime's mutating tools. It cannot provide isolation against arbitrary external editors/processes writing during a shell operation; strict external-writer attribution still requires isolated workspaces or OS filesystem provenance. It does not label an entire later repository delta as the current task's work.

Browser recipes support render and interaction modes. Render mode requires a fresh navigate/state operation, strict page assertions and a delivered screenshot; interaction mode retains its executed-interaction requirement. A static geometry check needs no invented click. Infrastructure failures block the check and prevent further screenshot/input attempts against the same unknown operation. Source-generation fingerprints and model delivery receipts remain mandatory; a build or screenshot file alone never becomes an acceptance pass.

## Measurement and regression

Terminal events publish model round trips, model wait/rejected/queue/time-to-content/generation measurements, request preparation, recovery wait, tool wall-time union and residual runtime time. Concurrent tools do not double-count wall time. Missing streaming timings remain missing samples, not invented latency measurements. Existing durable logs can be inspected with scripts/audit_turn_latency.py without executing any task or calling a provider.

tests/test_execution_efficiency.py verifies cross-route service contracts, permission-preserving deferred dispatch, partial batch safety, bounded sampling, excessive Retry-After, unresolved-operation convergence, retained browser receipts, workspace ownership, linear observation reduction and a million-character history projected before its first model call without losing durable evidence. The measured synthetic replay uses one model request and stays below the working budget; this is not a live-provider end-to-end speed claim.
