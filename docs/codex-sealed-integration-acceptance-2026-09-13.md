# Loom × Codex sealed integration acceptance — 2026-09-13

## Purpose

This document records the central integration state after Windows 01–07 were sealed at their own ownership boundaries and composed on `codex-sealed-preintegration-v1`.

It is an integration acceptance record, not a claim that Loom has complete product parity with every current Codex feature. The accepted architecture remains:

`Codex canonical runtime contract -> Python equivalent representation -> Loom durable integrity envelope`.

The project Codex research pin remains `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1`. Several worker windows also performed a later freshness audit against `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`; that later read did not silently replace the project pin.

## Sealed window heads included in integration ancestry

| Window | Scope | Sealed head |
| --- | --- | --- |
| 01 | Core Turn / Step / Runtime | `5fc19ba7a450fa4678642ff4834d5121e9f1ec24` |
| 02 | Exec / Sandbox / Approval / Permissions | `58e1cd42fae233caaaaf18e4a2d491b18d08dda0` |
| 03 | Context / Compaction / Instructions / AGENTS | `898db8e465f024f2422ed2ddc7a6fe9befc87737` |
| 04 | MCP / Tools / Network | `8fbe397602dbdd7c3b4106e214560e83b300c970` |
| 05 | App Server / Protocol / Electron UI | `5620e9289f54b4aa67a866711fee4335c087430f` |
| 06 | Persistence / Recovery / Session | `7f1940a2973505b834ca526ce370c4fe68cda682` |
| 07 | Parity Tests / CI / Hardening | `ba2a29238e13145a3746753b044a874e4465ba83` |

Central GitHub ancestry checks confirmed every head above is an ancestor of the preintegration branch (`behind_by = 0`, with the window head as merge base).

`main` remains unchanged at `b3dc3db50b117615be59b79806a6fd8fd38b02a8` while this acceptance pass is in progress.

## Central cross-window corrections

### 1. Approval resume must consume the original sampled StepContext

Window01 establishes that a model tool call belongs to the exact StepContext used to sample it. Window02's sealed Sandbox runtime still rebuilt a Step while validating approval resume. In isolation that was locally reasonable, but in the composed stack it could re-read live MCP/settings/sandbox/instruction state while the user was reviewing an older sampled action.

The integration branch therefore changes approval validation to use `_captured_step_context(...)`. The queued action, approval binding, `AdditionalPermissionProfile`, exec action identity, router and MCP authority now remain attached to the originally sampled Step. A missing captured Step fails closed.

This is a central composition fix; it does not redefine either window's ownership contract.

### 2. ToolSearch must not replace exact McpBinding execution authority

Window04 makes `McpBinding` / `PreparedMcpCall` the executable MCP authority for a Step. The long-lived tool registry is only a compatibility/discovery surface.

The composed ToolSearch layer previously rebuilt its router from `self.tools`, which could replace a Step-scoped MCP handler with a stale registry projection after the exact binding had already been captured.

The integration branch now starts ToolSearch exposure/schema-pressure planning from the router returned by lower Step-building layers. Exact Step tools win. Registry tools may supplement discovery only, and stale `mcp-binding:` projections cannot override Step execution authority.

### 3. Restarted pending approval remains fail-closed

Window02 is now sealed and integrated, but this does not make a pre-restart pending approval safely resumable. The original sampled StepContext, process-local approval cache and process-local action authority are not durably reconstructed as equivalent executable objects.

Therefore `WAITING_APPROVAL` recovery after process restart remains intentionally rejected. A safe handoff may continue an unfinished running turn only when there is no unresolved approval/tool execution authority. This is a deliberate safety boundary, not unfinished Window02 wiring.

## Accepted composed runtime ownership

The integrated stack now follows these central rules:

- `StepContext` is the immutable per-sampling execution world.
- model-visible tool definitions and executable `ToolRouter` handlers come from the same Step-scoped source.
- `StepContext.mcp_binding` holds exact MCP execution authority; `mcp_binding_json` and `binding_key` are diagnostics/integrity projections only.
- tool calls from one model response continue on the exact sampled Step rather than rereading live manager/registry/settings state.
- `ToolOrchestrator` owns approval requirement and sandbox retry planning.
- only typed/centrally classified sandbox denial may enter the one-shot retry path.
- `AdditionalPermissionProfile` is explicit scoped authority and is not reduced to a binary sandbox/no-sandbox toggle.
- Loom durable action bindings protect pending action integrity without replacing Codex runtime/cache-key boundaries.
- rollout/history/session persistence owns durable history; it does not pretend a live approval waiter or captured Step survived process death.

## Integration contracts added or corrected

Central contracts now cover:

- same-process approval resume does not rebuild the sampled Step;
- exact MCP `PreparedMcpCall` survives ToolSearch activation and schema planning;
- deferred approval after runtime restart fails closed with no tool side effect or new model request;
- safe recovery of a running unfinished turn preserves its turn id and adds no synthetic user message;
- safe handoff rejects unresolved approval/execution authority;
- exact MCP binding/revision changes produce distinct future Steps while old Steps retain their original authority.

These are committed contracts. They are not reported as executed until a runner actually runs them.

## Explicit remaining parity/product gaps

The integration acceptance does not erase window-scoped partials. Important remaining gaps include:

- Window02: full per-environment granted permission accumulation, arbitrary denied-read preservation, complete Codex exec-policy/danger classification and persisted amendments, retained stdin/write approval semantics, full network-service integration, multi-environment authority, and full Linux permission-profile/network enforcement breadth.
- Window04: complete Codex Apps trusted provenance (`plugin_id` / connector/link provenance) and the full Codex MCP `PermissionProfile` surface are not modeled. Those values must not be synthesized.
- Window05: Electron still uses the documented correlated-notification compatibility adapter rather than true server-initiated JSON-RPC request handling; approval decisions exposed by Loom are a subset; file-change approval, MCP elicitation/approval and request-user-input require dedicated runtime boundaries; the outer Loom protocol remains v1.
- Window03: unsupported `body_after_prefix` auto-compaction scope and other provider/feature interfaces remain explicit adaptations rather than silently guessed behavior.
- Cross-process pending approval continuation remains unsupported by design until Loom can regenerate fresh authority from a trustworthy durable contract.

Accordingly, the correct maturity statement is: **accepted 01–07 composed parity candidate within the documented scope, with explicit product gaps; not complete Codex product parity.**

## Executable validation state

The ChatGPT execution environment used for this integration cannot resolve GitHub for a local clone, so it cannot honestly report local pytest results.

Worker PR Actions repeatedly showed infrastructure/scheduling non-execution (`steps: []` / no runner work) rather than test execution. The preintegration stack therefore requires its own Draft PR CI observation.

Until an integration workflow actually executes, the correct status is:

**contract committed; static/ancestry integration reviewed; executable pytest/build/smoke result unknown.**

A red workflow with no executed steps must not be described as a test failure. A green claim requires evidence that the relevant jobs actually acquired runners and executed their test/build steps.

## Main merge hold

Do not merge the sealed preintegration branch to `main` solely because Windows 01–07 are sealed.

Central merge authorization requires:

1. a Draft integration PR from `codex-sealed-preintegration-v1` to the unchanged `main`;
2. review of the final PR diff for unintended cross-window rollback;
3. executable CI evidence where available, or an explicit unresolved infrastructure hold if jobs still do not run;
4. no newly discovered violation of StepContext, approval, MCP authority, recovery, context or protocol ownership.

Until those conditions are satisfied, `main` remains frozen.