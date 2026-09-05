# Loom App Server v1

`loom-app-server` is Loom's local control-plane boundary for rich clients. It is an adapter over the existing Agent Runtime, not a second model/tool loop.

The authoritative execution and persistence layers remain:

- `AgentRuntime` and its Runtime v2 wrapper stack;
- `FileAgentSessionStore` (`session.json` plus `events.jsonl`);
- `ToolOrchestrator` / `PermissionEngine`;
- Browser, MCP, Tool Search, Skills, Code Mode, sandbox, memory, Goal/Queue, and AgentGraph layers.

## Transport

The v1 transport is stdio JSON-RPC 2.0 over newline-delimited JSON (JSONL).

```powershell
loom-app-server --workspace C:\path\to\project --permission-mode workspace
```

`stdout` is reserved for protocol frames. Each request/response/notification is one UTF-8 JSON object per line. A single inbound message is capped at 1 MB.

Ingress and outbound queues are bounded. Saturated request ingress returns JSON-RPC error `-32001`. Notifications may be dropped under sustained client backpressure because authoritative state can be reconstructed with `thread/read`; request responses are not silently dropped.

## Initialization

Clients initialize with protocol version `1`:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"loom-client","version":"0.1"}}}
```

The response includes server identity, thread/turn/approval capabilities, notification names, runtime defaults, and streaming capabilities. With the standard configured Loom runtime, Phase 2.2 reports provider-backed assistant/tool streaming as enabled and explicitly reports `privateReasoning: false`.

## Core primitives

### Thread

A Thread maps to one durable Loom `AgentSession`. It includes a stable id, workspace, permission mode, status, current Turn id, optional fork provenance, timestamps, and cumulative usage.

### Turn

A Turn is one user-initiated unit of Agent execution. App Server launches it asynchronously and returns its id immediately while Runtime continues in the background.

### Item

Items are observable Turn content:

- `user_message`;
- `assistant_message`;
- `tool_call`;
- `approval`;
- `process`;
- `file_edit`;
- `error`.

Items never expose private model chain-of-thought.

## Methods

The stable v1 methods are:

- `runtime/status`
- `thread/start`
- `thread/resume`
- `thread/list`
- `thread/read`
- `thread/fork`
- `turn/start`
- `turn/interrupt`
- `approval/respond`

`thread/read` is the reconnect/recovery primitive. It returns normalized Turns/Items, canonical messages, pending approval, durable observable Agent events, final text, and error state.

`thread/fork` currently forks the latest inactive durable boundary. Historical partial-turn fork boundaries remain part of the Project/Worktree milestone.

All approved tools still execute through the existing Loom `ToolOrchestrator`, PermissionEngine, sandbox/process, and tool-handler path.

## Notifications

The server emits:

- `thread/started`;
- `turn/started`;
- `item/started`;
- `item/delta`;
- `item/completed`;
- `approval/requested`;
- `turn/completed`.

Tool/process/file events continue to originate from real Runtime events.

## Phase 2.2 provider streaming

The same `item/delta` protocol introduced in Phase 2.1 now carries genuine provider-backed assistant deltas where the configured provider supports streaming.

For a streamed assistant model step the App Server uses a stable live item id:

```text
assistant:step:<step_id>
```

The sequence is:

1. first public assistant chunk -> `item/started`;
2. provider text chunks -> one or more `item/delta` notifications;
3. stream completion metadata -> `item/delta` metadata;
4. canonical Runtime `MODEL_RESPONSE` is durably committed;
5. App Server sends `item/completed` for the same item id.

`thread/read` reconstructs the durable assistant item with the same step-based id, so a reconnect agrees with the live item identity.

Tool-call argument fragments are also normalized and forwarded when supplied by the provider. The final tool call still enters Runtime only after its JSON arguments have been fully reconstructed and validated.

Provider chunks themselves are transient: they are not appended to `events.jsonl` and do not become partial canonical messages. Missing a notification therefore does not corrupt the Turn.

If Runtime is embedded with a platform that does not expose streaming hooks, the App Server remains compatible and falls back to the Phase 2.1 full-response behavior.

See `docs/provider-streaming.md` for the full Phase 2.2 contract.

## Security and persistence

- API credentials remain inside Runtime/provider objects and never enter App Server metadata.
- Public assistant text may stream; private reasoning fields are intentionally ignored.
- The final canonical assistant message is committed atomically.
- Every side effect still crosses the existing permission/sandbox boundary.
- App Server does not claim Windows OS sandboxing; that remains Phase 2.6.
- The global user-prompt plaintext secret persistence gap is not solved by streaming; Secret/Vault and transcript sealing remain Phase 2.6 work.
- No local socket/WebSocket listener is exposed in v1. Stdio remains the rich-client transport.

## JSON-RPC error codes

| Code | Meaning |
| ---: | --- |
| `-32700` | Parse error |
| `-32600` | Invalid request |
| `-32601` | Method not found |
| `-32602` | Invalid params |
| `-32603` | Internal server error |
| `-32001` | Server overloaded; retry later |
| `-32002` | Connection not initialized |
| `-32003` | Connection already initialized |
| `-32004` | Requested durable resource not found |
| `-32009` | Runtime/thread state conflict |
| `-32010` | Unsupported protocol version |

## Current contract

A client can initialize, create/resume/list/read/fork a Thread, start/interrupt a Turn, answer approvals, receive true provider-backed assistant deltas, disconnect, reconnect, and rebuild authoritative durable state without importing `AgentRuntime` directly.
