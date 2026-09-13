# Codex Turn / Step lifecycle parity

Window 01 aligns Loom's core Turn -> Step -> model request -> tool dispatch lifecycle with
`openai/codex` at commit `1715e55076737158ba61d43158ede504de6d4ce1`.

This slice is stacked on Loom PR #123 (`codex-apply-patch-action-v1`) and does not merge or
rewrite the execution-action, MCP/network, compaction, app-server, or durable-recovery layers.

## Upstream lifecycle

Codex separates mutable turn ownership from immutable request ownership:

```text
Turn admitted
    |
    v
TurnContext
  initial_settings
  current_settings  <--- updates target future steps
    |
    v
capture_step_context()
    |
    v
Arc<StepContext>
  ResolvedStepSettings
  token budget / model telemetry
  environment + capability snapshot
  exact MCP binding
  finalized ToolRouter
  loaded AGENTS.md
    |
    +----> model sampling request
    |          |
    |          +---- transport retry: same StepContext / same request world
    |          |
    |          v
    |      sampled response
    |          |
    +----------+----> every tool invocation from that response
                         keeps the same Arc<StepContext>
                         |
                         v
                     observations
                         |
                         v
                 next semantic model sampling
                         |
                         v
                 capture a new StepContext
```

The important boundary is object ownership, not merely hashing mutable state. Once sampling has
captured a step, live turn/config/catalog changes do not retarget that response's actions. A
retryable transport failure is still an attempt to deliver the same sampling request, so it keeps
that same captured world; a recovery prompt after a rejected semantic response is a new sampling
request and may capture a newer world.

## Loom before this slice

Immediate tool execution already passed the same local `step` object from `TurnRunner` into the
pending-tool loop. The durable approval path was different:

```text
_build_step_context()
    |
    v
sample response -> queue calls
    |
    +---- immediate execution: same StepContext
    |
    +---- approval wait
             |
             v
        resume_approval()
             |
             +---- _build_step_context(same step_id) for validation
             |
             +---- _build_step_context(same step_id) again for execution
```

Those rebuilds re-read live registry/instructions/model/context/sandbox state. A digest could detect
some drift, but it could not provide Codex's stronger guarantee that the sampled action owns the
execution world it was admitted against.

## Loom after this slice

`AgentRuntime._capture_step_context()` is now the single capture boundary used by the canonical
`TurnRunner` model loop. It invokes the existing cooperative `_build_step_context()` MRO first, so
subclasses can finish their step planning, then retains the final object by session/turn/step id.

```text
TurnRunner
   |
   v
_capture_step_context()
   |
   +---- complete existing _build_step_context() MRO
   |
   +---- retain exact StepContext object
   |
   v
prepare ChatRequest from captured profile/reasoning/limits/router
   |
   +---- retryable transport error: retry exact same ChatRequest + StepContext
   |
   v
sampled response
   |
   +---- no tools: release step at terminal/steering boundary
   |
   +---- tools: same StepContext is passed to every call
             |
             +---- approval wait keeps it retained
             |
             +---- approval resume retrieves it; never rebuilds it
             |
             +---- all observations complete -> release it
```

If a pending action has lost its captured StepContext, Loom fails closed rather than reconstructing
an apparently equivalent world from live state.

## Source mapping

| Codex source / concept | Codex responsibility | Loom equivalent | This slice |
| --- | --- | --- | --- |
| `session/turn_context.rs::TurnContext` | turn-owned mutable/future state | `AgentSession` + runtime-owned turn services | preserve; do not create a parallel turn object |
| `session/step_settings.rs::ResolvedStepSettings` | immutable model/request settings version | `StepContext.world_state`, `RequestStateSnapshot`, `StepContext.reasoning` | freeze request-visible model/profile/reasoning/limits on step |
| `session/step_context.rs::StepContext` | one request-scoped execution world | `app/agent_runtime/step.py::StepContext` | retained as the authoritative object |
| `Session::capture_step_context*` | resolve then capture exact request world | `AgentRuntime._capture_step_context` | new final-capture boundary |
| `turn.rs` request loop | capture once before semantic sampling | `TurnRunner.run` | switched from `_build_step_context` to capture API |
| `run_sampling_request` retry ownership | retry transport against one captured request world | inner transport retry loop | reuse exact `StepContext` and prepared `ChatRequest` |
| `ToolRouter` | advertised specs + matching executable runtime | Loom `ToolRouter` | same captured router is advertised and dispatched |
| `ToolInvocation { step_context: Arc<_> }` | tool gets exact sampled request state | `_process_pending_tools(..., step=step)` / approval lookup | reuse exact Python object |
| `CancellationToken` / active task | prevent stale work after cancellation | Loom cancellation token + pending state | release captured steps on terminal/cancel/limit/failure |

## Frozen values

For the Core lifecycle, the captured Step now owns or references the values used for the model
request and subsequent tool dispatch:

- profile id;
- reasoning request;
- resolved context limits/output reserve;
- system/project instructions and communication language snapshot;
- permission snapshot;
- shell environment policy;
- workspace/world state and sandbox overlay;
- finalized `ToolRouter`, including cloned schemas and handler bindings;
- MCP identity snapshot supplied by the configured-MCP layer.

A later semantic capture may observe live updates. An already-captured step does not. Retryable
transport errors do not create a new semantic capture.

## Python adaptation of Codex ownership

Rust Codex propagates `Arc<StepContext>`. Loom uses normal Python object references plus a
runtime-owned ephemeral retention map keyed by `(session_id, turn_id, step_id)`.

The semantic contract is the same:

1. sampling captures one immutable object;
2. transport retries retain that object and the already-prepared request;
3. accepted response actions keep that object alive;
4. approval resume retrieves that object rather than rebuilding it;
5. the object is released after its observation boundary or terminal/cancellation failure;
6. a missing retained object fails closed.

The retention map is deliberately not a second durable state machine. Persisting/restoring an
immutable pending Step across process restart is Window 06's durability concern; this slice does
not invent a new persistence format.

## Contract tests

The Window 01 tests cover:

- immutable request state and action-binding identity;
- lower-level Core capture without relying on `SandboxAgentRuntime`;
- old Step stability plus new-Step visibility after AGENTS/tool/reasoning updates;
- profile/permission/environment-policy drift between captures;
- transport retry preserving one exact Step and one prepared request despite live tool/reasoning drift;
- two tool calls from one sampled response sharing the exact same Step object;
- next semantic model sampling receiving a new Step;
- approval resume continuing with the sampled router/handler despite live catalog and AGENTS drift;
- lost captured Step failing closed instead of rebuilding;
- cancellation clearing the pending Step and preventing execution;
- invalid tool arguments becoming a model-visible failed observation while the Turn continues.

## Stack assessment

Window 01 is intentionally stacked after #121 -> #122 -> #123.

- #121 introduced the request-state snapshot/binding identity this slice builds on.
- #122 introduced typed execution-action identity.
- #123 extended typed identity to apply-patch actions.
- Window 01 changes lifecycle ownership only; it does not replace those action identities.

The preferred integration order remains `#121 -> #122 -> #123 -> Window 01` unless the earlier PRs
are first merged/rebased in that same dependency order.

## Remaining cross-window debt

These are intentionally not solved here:

1. **Exact MCP connection ownership (Window 04).** Loom freezes the finalized MCP tool handlers in
   the Step router and records an MCP identity snapshot, but MCP connection/catalog internals remain
   owned by the MCP runtime. Codex carries an exact `Arc<McpBinding>` in StepContext; Window 04 should
   expose an equivalent immutable binding without creating another request lifecycle.
2. **Process-restart approval recovery (Window 06).** The in-process captured Step is ephemeral.
   After a process restart, a pending action must not be reconstructed from live state; this slice
   therefore fails closed. Window 06 may persist/restore a safe immutable execution binding.
3. **Capability/skills executable binding.** `active_skills` is still supplied through runtime
   services. If a skill handler can resolve mutable live state after sampling, the owning capability
   window should provide a frozen executable binding analogous to the tool router.
4. **Sandbox layer duplication.** `SandboxAgentRuntime._build_step_context()` still reconstructs the
   request-state snapshot synchronously while adding its sandbox snapshot. Because the final object is
   retained only after the full MRO returns, this does not reopen the approval drift bug, but the
   duplicate capture should eventually collapse to one Core capture implementation.
5. **Tool Search comment cleanup.** `ToolSearchRuntime.resume_approval()` still contains a comment
   describing the old parent behavior as rebuilding StepContext. The executable path now consumes the
   retained Step and remains in this single lifecycle; the stale comment belongs with that layer's next
   cleanup rather than expanding Window 01's code surface.

## API freeze assessment

The lifecycle shape is now close enough to Codex to treat these interfaces as the intended stable
Core boundary:

- `StepContext` is request-scoped and immutable;
- `_capture_step_context()` is the semantic sampling boundary;
- retryable transport delivery retains the captured Step/request;
- tool execution consumes a captured Step, never a reconstructed one;
- future/live updates become visible only on the next semantic capture.

Do not freeze persistence representation for captured Steps yet; that belongs to Window 06. Do not
freeze MCP binding internals yet; that belongs to Window 04.