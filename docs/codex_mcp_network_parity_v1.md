# Codex MCP / Network parity v1

## Baselines

- Codex source baseline re-read before implementation: `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1` (`main`, 2026-09-13).
- Loom base: `yuchenm1303-png/Loom@b3dc3db50b117615be59b79806a6fd8fd38b02a8`.
- Loom branch: `codex-mcp-network-parity-v1`.
- Draft PR: `#124`.

The implementation ports observable contracts/state rather than Rust syntax. No central runtime/orchestrator, sandbox core, app-server, context, or persistence file is modified by this window.

## Codex source mapping

### MCP binding

Primary sources:

- `codex-rs/codex-mcp/src/binding.rs`
- `codex-rs/codex-mcp/src/binding_tests.rs`
- `codex-rs/codex-mcp/src/binding_clients.rs`
- `codex-rs/codex-mcp/src/runtime.rs`
- `codex-rs/codex-mcp/src/connection_manager.rs`
- `codex-rs/codex-mcp/src/connection_manager/tool_catalog.rs`
- `codex-rs/core/src/session/mcp.rs`
- `codex-rs/core/src/session/mcp_runtime.rs`
- `codex-rs/core/src/session/mcp_refresh.rs`
- `codex-rs/core/src/mcp_tool_exposure.rs`
- `codex-rs/core/src/tools/spec_plan.rs`
- `codex-rs/core/src/tools/router.rs`
- `codex-rs/core/src/session/step_context.rs`

Observed Codex contract:

1. `McpBinding` is an immutable model-step semantic snapshot containing the model-visible tool catalog, exact ready clients, config/permission authority, server metadata/plugin provenance, and prepared-call map.
2. Compatible steps reuse the same binding only while the stable per-client catalog revisions remain equal. An unstable transient capture is not installed as the cache authority.
3. `McpBindingClients` captures exact ready clients. Execution does not resolve a same-named replacement client from a live registry.
4. `PreparedMcpCall` captures client, config, server/tool metadata and catalog revision. Catalog-revision validation encloses preparation/execution; a stale call rejects before irreversible preparation.
5. A closed captured client fails. It is never silently rerouted to a replacement client.
6. Connection reuse and semantic binding reuse are separate decisions. Connection config/startup/OAuth/client state govern connection reuse; catalog revision additionally governs binding reuse.
7. `StepContext` holds the exact `Arc<McpBinding>` used to derive the step's tool router/specs.
8. Tool exposure/search/filtering derives from that same binding. Deferred tool search does not escape the binding to a live MCP registry.
9. The binding is an in-memory capability object, not a durable JSON fingerprint. Runtime auth tokens/credentials are not serialized into a durable binding identity.

### MCP approval

Primary sources:

- `codex-rs/core/src/mcp_tool_call.rs`
- `codex-rs/core/src/tools/approvals.rs`

Observed Codex contract:

- MCP approval is not reduced to a generic sensitive-tool cache.
- `McpToolApprovalKey` contains `server`, `plugin_id`, `connector_id`, `link_id`, and `tool_name`; tool arguments are not in that session key.
- `ApprovedForSession` is stored in a dedicated MCP session approval store.
- Persistent approval is a policy/config amendment path and is distinct from the session cache.
- Approval authority is derived from the frozen prepared call's metadata/config, not from a fresh live server lookup.

### Network approval / policy

Primary sources:

- `codex-rs/core/src/tools/network_approval.rs`
- `codex-rs/core/src/network_policy_decision.rs`
- `codex-rs/protocol/src/approvals.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/permissions.rs`
- MCP/network approval tests under `codex-rs/core/tests`

Observed Codex contract:

- `NetworkApprovalContext` is `host + protocol`.
- Supported protocols are `http`, `https`, `socks5_tcp`, `socks5_udp`.
- Port is not in `NetworkApprovalContext`; it is part of the concrete access/session-cache key.
- Session host identity is `environment_id + lowercase host + protocol + port`.
- Pending-generation identity additionally includes `turn_id + execution_id` so an old request cannot resolve a newer same-host request.
- Session approved and denied host sets are separate; deny is checked fail-closed.
- Persistent `NetworkPolicyAmendment` is `host + Allow|Deny`; the approval context supplies the protocol for the exec-policy rule.
- The network approval flow is active only for managed network enforcement, non-`Never` approval policy, and a managed permission profile.
- Redirect/host drift is handled at the enforcing proxy/request layer: a request to a different host/protocol/port produces a different concrete identity and therefore does not inherit an unrelated session approval.
- Network approval does not replace sandbox permission enforcement. Retry must preserve the original permission profile/sandbox constraints.

## Loom implementation

### Exact MCP binding authority

`app/agent_runtime/mcp_runtime.py` now provides:

- `McpBinding`
- `PreparedMcpCall`
- per-connected-client `catalog_revision`
- frozen `MCPServerConfig` and `MCPServerMetadata` capture inside each prepared call
- binding reuse by exact current connection/config object + catalog revision signature
- `refresh_tools()` that increments catalog revision only when the semantic tool catalog changes
- `reconnect_server()` that publishes a replacement ready client while existing bindings keep the old exact client
- model-visible `AgentTool.handler` closures that hold `PreparedMcpCall` directly

`MCPClientManager.call_tool(server, tool, ...)` remains only as a compatibility API. Model-visible handlers do not use it. This removes the old prompt/catalog-to-live-registry TOCTOU path.

`AgentTool.binding_key` is retained as a secret-free diagnostic/compatibility projection. It is not execution authority and is not a substitute for `McpBinding`.

### MCP approval primitives

`app/agent_runtime/mcp_approval.py` provides the exact Codex-style structured approval key plus a dedicated session-approval cache. It intentionally does not mutate the central approval orchestrator owned by window02.

### Network primitives

`app/agent_runtime/network_approval.py` provides:

- `NetworkApprovalProtocol`
- `NetworkApprovalContext`
- `NetworkAccessIdentity`
- `PendingNetworkApprovalKey`
- `NetworkApprovalSessionCache`
- `NetworkPolicyAmendment`
- protocol-specific exec-policy amendment projection
- a pure approval-flow gate for managed-network / approval-policy / permission-profile integration

This window does not install those primitives into the central retry/orchestrator or app-server protocol because those files are owned by windows 02 and 05.

## McpActionIdentity decision

No `McpActionIdentity` was added.

Codex does not need a separate action-identity abstraction to solve this problem. Its authority boundary is the `McpBinding` plus `PreparedMcpCall`, with a dedicated structured `McpToolApprovalKey` for approval caching. Adding another Loom-only identity would duplicate or blur those boundaries and make parity harder to reason about.

## Contract tests translated from Codex

Added tests cover:

- same binding reused across compatible catalog state
- catalog revision change creates a new binding
- stale prepared call rejects before preparation side effects
- old binding never silently executes a replacement client
- model-visible handler captures the exact prepared client
- closed captured client fails instead of rerouting
- server/tool identity separation
- frozen config and server metadata authority
- runtime secret not present in the diagnostic binding projection
- dedicated MCP approval key/session cache
- network environment/host/protocol/port separation
- redirect/host drift not inheriting a host approval
- session deny precedence
- pending turn/execution generation separation
- policy amendment protocol projection
- managed-network / policy / permission-profile gating

## Cross-window interface requirements

### Window01: runtime / step construction

Minimum required interface:

1. Before building a step's tool router, obtain one `McpBinding` from the MCP manager/runtime.
2. Store that exact object on Loom's step context (Codex `StepContext.mcp` equivalent).
3. Derive the step's MCP `AgentTool`s/router entries from that binding, not from the manager's live registry.
4. On a later compatible step, reusing the same binding object is valid while the manager returns the same stable binding; refresh produces a new object.

Without this integration, `MCPRuntime` still registers its startup MCP tool set into the long-lived Loom registry. The handlers are now safe exact handles, but dynamic per-step catalog refresh is not yet wired into the central step builder.

### Window02: approval / retry / sandbox orchestrator

Minimum required interface:

1. MCP review requests must receive the structured `McpToolApprovalKey` and prepared-call authority rather than relying only on `ToolEffect.SENSITIVE`.
2. `ApprovedForSession` must consult/update the dedicated MCP session cache; persistent approval must go through policy/config amendment, not that cache.
3. Network review must use `NetworkAccessIdentity` for session cache lookup and `NetworkApprovalContext` for the user/protocol payload.
4. A denied network identity must win fail-closed over stale session approval.
5. Retry after network approval must preserve the original sandbox/permission profile and must not promote network approval into a general sandbox bypass.
6. Persistent network allow/deny must serialize via the existing policy persistence owner rather than this module writing persistence directly.

### Window05: app-server protocol

Minimum required interface:

1. Expose `NetworkApprovalContext { host, protocol }` with the four Codex protocol values.
2. Carry network review decisions including `Approved`, `ApprovedForSession`, `NetworkPolicyAmendment`, and `Abort` according to the app-server protocol owner.
3. Carry additional network permission fields without collapsing them into the network approval cache identity.

## Restart and durability

`McpBinding`, `PreparedMcpCall`, exact clients, and catalog revision authority are intentionally non-durable. On process restart Loom reconnects/rebuilds the binding; it does not deserialize a capability handle. This matches the important Codex semantic boundary: secrets/live clients are not durable binding identity.

MCP/network session caches are likewise in-memory session state. Persistent approval uses policy/config persistence owned elsewhere.

## Remaining differences / risks

1. Loom currently eagerly connects enabled MCP servers and has no Codex-equivalent dormant cached tool catalog/startup-trigger model. If Loom later introduces dormant MCP servers, it should port Codex's dormant catalog revision semantics rather than extending this binding ad hoc.
2. `reconnect_server()` retains retired clients until `MCPClientManager.close()`. Codex naturally releases old clients with `Arc` lifetime when old bindings disappear. Loom's observable execution semantics are the same (old binding keeps old client), but resource lifetime is coarser and should be tightened if reconnect churn becomes material.
3. Per-step `McpBinding` ownership is not wired because `StepContext`/turn construction belongs to window01.
4. MCP approval primitives are not wired into the central reviewer/cache because that belongs to window02.
5. Network approval/policy primitives are not wired into central retry/proxy handling because that belongs to window02; protocol serialization belongs to window05.
6. The first PR CI run concluded `failure`, but every returned job had `steps: []`; per project rules this is not evidence that pytest/build failed and is recorded as CI-not-executed/unverifiable.

## Acceptance status

The MCP execution TOCTOU bug in this window's owned code is removed: a tool definition exposed from a binding executes the exact captured client/config/revision or fails closed. No `McpActionIdentity` is required.

Full product parity still depends on the three cross-window integrations above and must be accepted by the total-control/acceptance window before merge.
