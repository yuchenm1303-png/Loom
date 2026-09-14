# Codex app-server v2 → Loom protocol parity v1

## Baselines

- Codex upstream: `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1` (`main`, re-read 2026-09-13; unchanged from task baseline).
- Loom baseline: `yuchenm1303-png/Loom@b3dc3db50b117615be59b79806a6fd8fd38b02a8`.
- Work branch: `codex-appserver-protocol-parity-v1`.

This port follows Codex observable protocol semantics where Loom's current runtime exposes the required information. It does not claim wire-identical Codex v2 support where the Electron transport or Loom runtime lacks the corresponding boundary.

## Codex sources used as the contract

Primary protocol/runtime references:

- `codex-rs/app-server/README.md`
- `codex-rs/app-server/src/bespoke_event_handling.rs`
- `codex-rs/app-server/src/outgoing_message.rs`
- `codex-rs/app-server-protocol/src/protocol/common.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/item.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/permissions.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/mcp.rs`
- generated `schema/json/ServerRequest.json`
- generated `schema/json/ServerNotification.json`
- generated `schema/json/CommandExecutionRequestApprovalParams.json`

Test behavior was cross-checked against the v2 integration suite, especially `tests/suite/v2/thread_resume.rs` (running-thread rejoin/resume) and `tests/suite/v2/turn_interrupt.rs` (interrupt is bound to a concrete turn), plus the app-server test-client rejoin guidance.

## Mapping

| Area | Codex v2 observable contract | Loom counterpart after this port | Status / gap |
| --- | --- | --- | --- |
| thread / turn / item | Explicit `Thread`, `Turn`, tagged `ThreadItem`; item identity is stable | Durable session/turn reconstruction and transcript items; tool id remains `tool:<callId>` | Partial type parity; Loom keeps product-specific item names |
| item lifecycle | `item/started`, deltas, `item/completed` with thread/turn/item identity | Same notification lifecycle; same-call events reduce into one stable item | Aligned for existing Loom items |
| server request vs notification | Approval, user input and elicitation are server requests with JSON-RPC request ids; lifecycle is notification | Lifecycle remains notification. Approval uses `approval/requested` carrying a durable `requestId`, answered by `approval/respond` | **Transport deviation**; Electron bridge cannot answer server-initiated requests yet |
| command execution approval | `threadId`, `turnId`, `itemId`, `startedAtMs`, optional `approvalId`, structured reason/network context/decisions | `requestId`, `threadId`, `turnId`, tool `itemId`, Loom `approvalItemId`, `callId`, `startedAtMs`, structured metadata and decisions | Correlation semantics ported; response decision set currently only accept/decline |
| file-change approval | Dedicated `item/fileChange/requestApproval` request and typed response | No dedicated runtime event/request boundary currently exposed | Not yet portable in this window |
| MCP approval / elicitation | Typed MCP server elicitation request; nullable turn correlation where appropriate | Loom app-server currently receives no equivalent runtime event | Not yet portable in this window |
| network approval context | Structured `NetworkApprovalContext { host, protocol }`; network policy decisions are typed | Serializer passes structured `networkApprovalContext` only when runtime provides it | Current runtime does not emit it; never inferred from `reason` |
| request_user_input | Dedicated typed server request with question ids/options/secrecy/blocking | No equivalent app-server runtime event | Remaining gap |
| thread resume / rejoin | `thread/resume` reuses a live thread in-process; tests cover running-thread rejoin | `thread/resume` reconstructs durable transcript and exact pending approval identity; active in-process service state is readable | Waiting-approval parity improved; process-level running-turn rejoin remains weaker |
| reconnect current state | Running state plus pending server requests can be replayed/recovered | `thread/read` is canonical durable resync; pending approval is rebuilt from durable approval event | Approval survives refresh/reconnect |
| second client during active turn | Connection-scoped app-server can attach/replay eligible pending requests | Service can read active state, but current Electron/stdio topology is single client | Transport gap |
| cancellation | `turn/interrupt` is bound to `threadId + turnId`; stale turn is rejected | `turn/interrupt` now requires exact `turnId`; old approval response after cancel is rejected | Aligned fail-closed behavior |
| duplicate / stale response | Pending callback is consumed once; stale/duplicate response cannot drive runtime | Approval requires exact current `turnId + requestId + callId`; duplicate while first is active errors; after transition no pending approval exists | Aligned intent; explicit error rather than warning-only no-op |
| protocol versioning | Codex has explicit v2 types and generated schemas | Loom remains outer protocol v1 and advertises `approvalProtocol` capability | **Versioning deviation** until Electron transport can be changed |

## Approval wire: before / after

Before, the static snapshot lost the approval stage/correlation identity:

```json
{
  "pendingApproval": {
    "callId": "call-1",
    "toolName": "shell",
    "arguments": {"command": "echo ok"},
    "effect": "sensitive",
    "reason": "approval required"
  }
}
```

The response was only:

```json
{
  "method": "approval/respond",
  "params": {
    "threadId": "thread-1",
    "callId": "call-1",
    "approved": true
  }
}
```

After, both realtime `approval/requested.params.approval` and `thread/read.pendingApproval` use the same serializer:

```json
{
  "requestId": "evt-approval-7",
  "threadId": "thread-1",
  "turnId": "turn-9",
  "itemId": "tool:call-1",
  "approvalItemId": "approval:call-1",
  "callId": "call-1",
  "requestType": "toolExecution",
  "kind": "initial",
  "retryReason": null,
  "startedAtMs": 1789300800000,
  "toolName": "shell",
  "arguments": {"command": "echo ok"},
  "effect": "sensitive",
  "reason": "approval required",
  "permissionMode": "approval",
  "networkApprovalContext": null,
  "availableDecisions": ["accept", "decline"]
}
```

The response is correlated to the exact durable request and turn:

```json
{
  "method": "approval/respond",
  "params": {
    "threadId": "thread-1",
    "turnId": "turn-9",
    "requestId": "evt-approval-7",
    "callId": "call-1",
    "decision": "accept"
  }
}
```

For comparison, Codex's target transport is a true server request such as `item/commandExecution/requestApproval` with a JSON-RPC `id`; `itemId` identifies the command item under review, and the client responds to that request id. Loom's `requestId` is the durable runtime event id used to make the current notification/request adapter safe across refresh and reconnect.

## State machine

```text
TURN_STARTED
  |
  +-- TOOL_REQUESTED(callId) ------------------------------+
  |      item tool:<callId> = started/running              |
  |                                                        |
  +-- TOOL_APPROVAL_REQUIRED(callId, eventId)               |
         tool:<callId> = waiting_approval                   |
         approval:<callId> = waiting   (UI-only item)       |
         pendingApproval.requestId = eventId                |
         thread = waiting_approval                          |
                    |                                       |
          +---------+----------+                            |
          |                    |                            |
   correlated accept     correlated decline                 |
          |                    |                            |
   TOOL_APPROVED          TOOL_DENIED                       |
          |                    |                            |
   runtime executes       runtime does not execute          |
          |                    |                            |
   TOOL_COMPLETED/...     turn continues/completes          |
                                                               
Any response with stale turnId/requestId/callId -> reject before runtime.
Interrupt(current turnId) -> cancel; pending approval disappears; old response rejects.
Repeated events for the same callId reuse tool:<callId> and approval:<callId>.
```

## Reconnect / rejoin behavior

`thread/read` is the Loom resync source of truth. When a session is waiting for approval, `pending_approval_record()` searches durable events backwards for the current turn's matching `TOOL_APPROVAL_REQUIRED` event. The event id is reused as `requestId`, so a page refresh or a newly constructed app-server service produces the same correlation identity.

If a legacy/corrupt snapshot contains `pending_approval` but no matching durable event, Loom returns a display-only pending approval with `requestId: null`. The UI refuses to answer it, and the server independently refuses responses without the durable identity. This is intentionally fail closed.

## Electron-only presentation / transport differences

Loom intentionally keeps a separate `approval:<callId>` transcript item so the Electron UI can render an approval card. The item being authorized is still `tool:<callId>`, which is exposed as `pendingApproval.itemId`; `approvalItemId` is explicitly Loom UI metadata.

The current `desktop-react/electron/main.ts` bridge forwards every incoming frame with a `method` as a notification and has no path to send a JSON-RPC response to a server-initiated request. That file is outside this window's write ownership. Therefore this phase cannot truthfully implement Codex's bidirectional server-request transport and uses a correlated adapter instead.

## Source mismatch discovered during mapping

The task brief said current `PendingToolApproval` already had `kind` / `retry_reason`. At Loom baseline `b3dc3db50b117615be59b79806a6fd8fd38b02a8`, `app/agent_runtime/contracts.py` and the emitted `TOOL_APPROVAL_REQUIRED` payload do **not** contain those fields; they contain `call_id/tool/arguments/effect/reason/permission_mode/step_id` plus the durable `AgentEvent.event_id`.

This app-server port therefore:

- passes explicit `kind`, `retry_reason` / `retryReason`, and structured network context through if a future runtime supplies them;
- classifies the current pre-execution permission boundary as `initial`;
- never guesses sandbox/network stages from the human-readable reason string;
- requires a separate runtime-owner change before sandbox/network/retry sub-stages can be represented with full Codex-like fidelity.

## Remaining protocol gaps / cross-window interface requests

1. **Electron bidirectional JSON-RPC:** teach the main-process transport to distinguish server request (`method + id`) from notification and return a response. Then replace the compatibility `approval/requested` notification with true server requests and a pending callback table/replay model like Codex `outgoing_message.rs`.
2. **Runtime approval metadata:** runtime owner should emit structured approval kind/retry reason and network/sandbox context rather than reason text. App-server serializer is ready to pass structured values through.
3. **File-change / MCP / request_user_input:** runtime needs explicit events/request boundaries before app-server can expose the Codex request families without inventing state.
4. **Running-turn process recovery:** current Loom `_load()` converts an orphaned persisted `RUNNING` session into interrupted state when it is not locally active. Codex can rejoin a running thread owned by the same app-server process and has stronger connection replay semantics. Full process-level live-turn rejoin is outside this app-server-only patch.
5. **Protocol version:** outer Loom protocol remains v1 because the owned React layer can be changed but the Electron initializer/transport cannot. The initialization result advertises `approvalProtocol` so clients can detect the stricter response contract. A future transport change should introduce a clean version boundary rather than pretending this adapter is Codex v2.
