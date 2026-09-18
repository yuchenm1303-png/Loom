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
- `project/list`
- `project/create`
- `project/rename`
- `project/remove`
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

## Projects

A Project is a named place conversations happen. Its **identity is its root
directory**: one root belongs to exactly one project, which is what keeps
"which project is this thread in?" a question with a single answer. Registering
a root that is already a project returns the existing one rather than failing.

The registry lives in `<loom home>/projects.json`, beside `models.json`. The App
Server owns it for the same reason it owns threads: a client must not be the
thing that decides what is real.

`project/list` returns every project with a `threadCount`, plus
`unfiledThreadCount` for threads no project claims. Listing is also where
adoption happens: any workspace that already has threads but no project is
registered on the spot, so the feature arrives already populated instead of
demanding a setup step. Pass `{"adopt": false}` to inspect without registering.

`thread/start` accepts `projectId` as an alternative to `workspace` — passing
both is an error. Every thread record now carries `projectId`, empty when the
thread is unfiled.

`project/remove` **unregisters** a project. It deletes no files and no threads;
the threads keep their workspace and become unfiled. The response says so
explicitly, so a client can word its confirmation honestly:

```json
{"projectId": "p…", "removed": true, "unfiledThreadCount": 2,
 "deletedThreads": false, "deletedFiles": false}
```

The roadmap's multi-root project is deliberately not built. A thread has exactly
one workspace, so multi-root immediately raises "which root does a new thread
get?" — better answered with worktrees in hand than guessed at now. Managed
worktrees and fork boundaries remain Milestone 2.5.

## Attachments

`thread/start` and `turn/start` also accept an optional `clientInputId` (maximum 256 characters). When present, App Server treats that write as durably idempotent for 30 days:

- the same `clientInputId` with the same normalized request returns the original thread / turn identity;
- the same `clientInputId` with a different request is rejected;
- the mapping survives App Server, Desktop, MCP adapter, and tunnel-client restarts because it is stored under the Loom runtime home in `app_server/idempotency.db`;
- `turn/start` persists a reserved turn id and staged attachment manifest before Runtime adoption, then uses durable `TURN_STARTED` evidence to decide whether a replay should resume the reserved turn or return the already-adopted one;
- callers that omit `clientInputId` keep the original non-idempotent protocol behavior.

Successful keyed responses include `idempotentReplay: true|false`. The replay ledger is bounded (30-day retention, 20,000-row cap); clients should generate a fresh id for each logical write rather than reuse human-readable labels.

`turn/start` accepts an optional `attachments` array alongside `input`:

```json
{
  "threadId": "…",
  "input": "what changed in this screenshot?",
  "attachments": [{"path": "C:/Users/me/Pictures/shot.png", "name": "shot.png"}]
}
```

Attachments are **local source paths**, not inline bytes. The App Server runs on
the same machine as its client, and a base64 screenshot would exceed the
transport's per-message limit long before it reached a model. A client with no
file behind its data — a clipboard image — writes a temporary file first.

The server copies each source into `<workspace>/.loom/attachments/<turn>/` and
then splits by kind:

- **images** become `ImagePart` content on the user message, so the model sees
  them;
- **every other file** is referenced only by its workspace-relative path, listed
  in a manifest appended to the user's text. The agent reads it with the file
  tools it already has. Nothing is inlined, so a 40 MB CSV costs no context.

Either kind alone is a valid turn: `input` is required only when `attachments`
is empty. The response echoes what was staged under `turn.attachments`.

Whether images are accepted is a property of the launch, not of the request.
`runtime/status` reports it, so a client can refuse an image in its composer
rather than discovering the problem as a failed turn:

```json
"attachments": {"images": true, "files": true, "maxCount": 10,
                "maxImageBytes": 8388608, "maxFileBytes": 134217728}
```

`images` follows `--vision/--no-vision`, which declares whether the bound model
can read them. Reading back a thread never returns image bytes; a message with
images renders as `[N images attached]` beside its text.

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


## Reliability additions (2026-09-08)

- `turn/steer`: `{threadId, turnId, input}` accepts a durable input for the matching
  active turn. A stale ID is rejected. Unexecuted actions from the old model response
  are cancelled at a safe boundary; an already executing side effect is not rolled back.
- `thread/resync`: when bounded notification delivery overflows, the writer emits
  `{threadId, reason: "notification_overflow", dropped}` after draining queued frames.
  Clients must discard their assumption of complete delta delivery and call `thread/read`.
- Model stream retries use a new step identity and close obsolete transient items.
  Incomplete responses never produce a successful turn terminal event.

The protocol remains Loom's own API; these additive methods are not a claim of complete
Codex wire compatibility. See [implementation status](alignment-implementation-2026-09-08.md).
