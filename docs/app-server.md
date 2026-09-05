# Loom App Server v1

`loom-app-server` is Loom's local control-plane boundary for rich clients. It is an adapter over the existing Agent Runtime, not a second model/tool loop.

The authoritative execution and persistence layers remain:

- `AgentRuntime` and its existing Runtime v2 wrapper stack;
- `FileAgentSessionStore` (`session.json` plus `events.jsonl`);
- `ToolOrchestrator` / `PermissionEngine`;
- Browser, MCP, Tool Search, Skills, Code Mode, sandbox, memory, Goal/Queue, and AgentGraph layers.

## Transport

Phase 2.1 exposes one transport:

```text
stdio JSON-RPC 2.0 over newline-delimited JSON (JSONL)
```

Start it with:

```powershell
loom-app-server --workspace C:\path\to\project --permission-mode workspace
```

`stdout` is reserved for protocol frames. Clients should not expect banners or human-readable logging there.

Each request/response/notification is exactly one UTF-8 JSON object per line. A single inbound message is capped at 1 MB.

The transport uses bounded ingress and outbound queues. If request ingress is saturated, the server returns JSON-RPC error `-32001` with `Server overloaded; retry later.` Notifications may be dropped under sustained client backpressure because clients can reconstruct authoritative state with `thread/read`; request responses are not silently dropped.

## Initialization

A connection must initialize before using any other request method.

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"loom-test","version":"0.1"}}}
```

Successful response includes:

- `protocolVersion`;
- server identity;
- supported thread/turn/approval capabilities;
- notification method names;
- runtime model/default-workspace/default-permission metadata.

The current protocol version is `1`. Unsupported versions fail with error `-32010`.

An optional `initialized` notification may be sent after the handshake:

```json
{"jsonrpc":"2.0","method":"initialized","params":{}}
```

## Core primitives

### Thread

A protocol Thread maps to one durable Loom `AgentSession`.

The Thread record includes:

- stable id;
- workspace;
- permission mode;
- status;
- current Turn id;
- optional `forkedFromId` provenance;
- timestamps and cumulative usage.

### Turn

A Turn is one user-initiated unit of Agent execution. The App Server launches it asynchronously and returns its id immediately while the actual Loom Runtime continues work in the background.

### Item

Items are observable turn content reconstructed from durable Agent events. Phase 2.1 normalizes:

- `user_message`;
- `assistant_message`;
- `tool_call`;
- `approval`;
- `process`;
- `file_edit`;
- `error`.

Items never expose private model chain-of-thought.

## Methods

### `runtime/status`

Returns protocol/runtime metadata, active thread ids, and app-server task failures.

### `thread/start`

Create a new durable Thread.

```json
{"jsonrpc":"2.0","id":2,"method":"thread/start","params":{"workspace":"C:\\work\\repo","permissionMode":"workspace"}}
```

If `workspace` or `permissionMode` is omitted, the server startup defaults are used.

### `thread/resume`

Load and return one existing durable Thread:

```json
{"jsonrpc":"2.0","id":3,"method":"thread/resume","params":{"threadId":"..."}}
```

If a persisted session still says `running` after its owning process disappeared, the existing Loom recovery path repairs/interrupts it before it is presented as authoritative state.

### `thread/list`

List saved Threads, newest first. v1 accepts `limit` from 1 to 200.

### `thread/read`

Return one authoritative Thread snapshot including:

- normalized Turns and Items reconstructed from durable events;
- canonical messages;
- pending approval;
- raw observable Agent events;
- final text/error.

This is the reconnect/recovery primitive when live notifications were missed.

### `thread/fork`

Create a new Thread by copying canonical durable history from an inactive source Thread.

Phase 2.1 intentionally supports only the latest durable boundary. Historical partial-turn fork boundaries are deferred to the Project/Worktree milestone (2.5). The new Thread persists `forkedFromId`, starts idle, and does not inherit a live process, approval, pending tool call, or execution stack.

### `turn/start`

Start an asynchronous Agent turn:

```json
{"jsonrpc":"2.0","id":4,"method":"turn/start","params":{"threadId":"...","input":"Inspect this repository and summarize it."}}
```

The App Server supplies a stable Turn id to the existing Runtime, then progress arrives as notifications.

### `turn/interrupt`

Request cancellation of the current Turn. If the Thread is waiting for approval, cancellation also clears that pending approval through the existing Runtime cancellation path.

### `approval/respond`

Resolve the current approval:

```json
{"jsonrpc":"2.0","id":5,"method":"approval/respond","params":{"threadId":"...","callId":"...","approved":true}}
```

The approved tool still executes through the existing Loom `ToolOrchestrator`, PermissionEngine, sandbox/process, and tool handler path.

## Notifications

The server emits:

- `thread/started`;
- `turn/started`;
- `item/started`;
- `item/delta`;
- `item/completed`;
- `approval/requested`;
- `turn/completed`.

Tool/process/file events are translated from real `AgentEvent` records only after the Runtime has durably recorded them.

## Streaming boundary in Phase 2.1

The protocol already has `item/delta`, but provider token streaming is **not** implemented in this milestone.

For assistant text, the current non-streaming provider adapter produces one complete `MODEL_RESPONSE`; App Server v1 therefore emits one full-text `item/delta` chunk followed by `item/completed`.

Phase 2.2 will replace that full-text chunk with true normalized provider deltas where supported, without changing the client-facing Thread/Turn/Item model.

Process output can already generate multiple real `item/delta` notifications because managed process output is observable incrementally today.

## Security and persistence

- API credentials remain inside Runtime/provider objects and are never included in App Server metadata.
- The protocol does not request or expose private chain-of-thought.
- Every side effect still crosses the existing permission/sandbox boundary.
- The App Server does not claim Windows OS sandboxing; that remains Phase 2.6.
- The existing global user-prompt secret persistence gap is not solved by this protocol layer; Secret/Vault and transcript sealing belong to Phase 2.6.
- No local socket/WebSocket listener is exposed in v1. Stdio is the only transport.

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

## Phase 2.1 exit contract

A client should be able to initialize, create/resume/list/read/fork a Thread, start and interrupt a Turn, answer an approval, receive observable notifications, disconnect, reconnect, and rebuild authoritative state using `thread/read` without importing `AgentRuntime` directly.
