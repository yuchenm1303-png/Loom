# Loom Phase 2.2 — Provider Streaming

Phase 2.2 turns the App Server's existing `item/delta` shape into genuine provider-backed streaming while keeping Loom's canonical conversation state atomic.

## Architecture

```text
OpenAI / OpenAI-compatible provider stream
                 |
        OpenAIStreamingChatBackend
                 |
          StreamingAIPlatform
                 |
     normalized ProviderStreamEvent
                 |
        StreamingAgentRuntime
                 |
       transient AgentStreamEvent
          /                \
 App Server notifications   Local Web snapshot
          |                 (debug/fallback UI)
      rich clients
```

This is an observer layer over the existing Runtime v2 drive loop. There is still only one model/tool loop.

## Provider contract

For normal Loom Agent turns, `StreamingAIPlatform` consumes the backend stream and reconstructs one final `ModelResponse` from:

- public assistant text deltas;
- streamed function/tool call ids, names, and JSON argument fragments;
- finish metadata;
- token usage when the provider supplies streaming usage metadata.

The OpenAI/OpenAI-compatible backend requests streaming usage with:

```python
stream=True
stream_options={"include_usage": True}
```

For OpenAI-compatible servers that explicitly reject `stream_options`, Loom retries the initial stream request once without that option. Streaming remains enabled, but usage can be unavailable for that step.

## Durable boundary

Provider chunks are deliberately **not** appended to `events.jsonl` and are not written into canonical message history.

The sequence is:

1. Runtime durably records `MODEL_REQUESTED`.
2. Provider chunks are emitted through the transient stream bus.
3. The stream accumulator validates and reconstructs the complete assistant response/tool calls.
4. Runtime appends exactly one final assistant message.
5. Runtime durably records `MODEL_RESPONSE` with the final text, tool calls, usage, finish reason, and response id.

A client disconnect therefore cannot leave a half-written assistant message in the durable thread.

## Normalized runtime stream events

`StreamingAgentRuntime` exposes a transient subscription surface with:

- `assistant_text_delta`;
- `tool_call_argument_delta`;
- `model_stream_completed`.

Every event is correlated with `session_id`, `turn_id`, and `step_id`.

The runtime stream is public observable execution state only. Provider-specific hidden reasoning fields such as `reasoning_content` are intentionally ignored and are never forwarded as an Agent stream primitive.

## App Server

`loom-app-server` now launches the streaming App Server adapter.

When provider streaming is enabled, initialization reports:

```json
{
  "providerStreaming": true,
  "runtimeStream": {
    "assistantTextDelta": true,
    "toolCallArgumentDelta": true,
    "usageCompletionMetadata": true,
    "privateReasoning": false
  }
}
```

Assistant items use a stable live id based on the model step:

```text
assistant:step:<step_id>
```

The App Server sends:

1. `item/started` on the first public assistant text chunk;
2. one or more real `item/delta` text notifications as provider chunks arrive;
3. completion metadata when the provider stream closes;
4. `item/completed` only after the canonical `MODEL_RESPONSE` has been durably committed.

`thread/read` remaps the reconstructed durable assistant item to the same step-based id so reconnect state agrees with the live stream identity.

## Local Web UI

The existing browser UI remains a local debug/fallback client rather than becoming a second Agent runtime.

It now receives the provider text accumulated so far in its normal snapshot response. The source data is real provider streaming, although the current browser transport still samples snapshots on the existing short polling interval. App Server clients receive the stream directly as notifications.

The partial Web UI message is presentation-only and is never written to the session store.

## Compatibility

`StreamingAIPlatform` starts with streaming disabled. This preserves the old synchronous completion behavior for detached/legacy users of the AI platform.

The top-level Loom `AgentRuntime` enables streaming when the configured platform exposes the streaming hooks. Test doubles and embedders that only implement `execute_chat` continue to work unchanged.

## Security

- API keys remain inside provider/runtime objects.
- Partial provider text is transient and not persisted until final response validation succeeds.
- Private chain-of-thought/reasoning streams are not exposed.
- Tool execution still crosses the existing ToolOrchestrator, PermissionEngine, and sandbox boundary.
- This milestone does not claim Windows OS sandboxing.
- The separate user-prompt plaintext secret persistence gap remains a Phase 2.6 concern.

## Exit criteria

Phase 2.2 is complete when tests prove that:

- multiple provider text chunks reach a subscribed client before final completion;
- streamed tool arguments reconstruct into the exact final ToolCall JSON;
- streaming usage/response metadata reaches the final canonical response when supplied;
- transient chunks are absent from durable `events.jsonl` and canonical history;
- App Server live item identity matches reconnect `thread/read` identity;
- local Web snapshots can display partial assistant text without persisting it;
- legacy non-streaming platform users retain their old behavior.
