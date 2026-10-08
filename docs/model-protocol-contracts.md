# Model protocol boundary

Loom supports Chat Completions, Responses and Messages through provider adapters.
The agent consumes `ModelResponse` / `StreamEvent`; it does not interpret provider
item IDs, SSE envelopes or provider status strings itself. Model routing and
reasoning options remain provider configuration, not task-completion heuristics.

## Ownership

| Component | Responsibility |
| --- | --- |
| `openai_streaming.py` | Chat Completions wire deltas, usage-only tail, transport closure |
| `responses_protocol.py` | Responses item/index/call identity and delta/snapshot reconciliation |
| `sse.py` | Messages SSE framing, multiline data, comments and UTF-8/JSON validation |
| `opencode_go_runtime.py` | Provider requests, Responses/Messages event routing, usage and error conversion |
| `tool_protocol.py` | Shared atomic tool JSON, identity, duplicate and output-limit validation |
| `streaming_platform.py` | Accumulate normalized deltas; commit one complete response |

Responses `item.id` and `function_call.call_id` are different namespaces.
Argument delta events refer to an item; history and tool results refer to a call.
Indexes must agree with known identities. Multiple calls may interleave. A reused
call ID or contradictory identity is rejected, never routed to a guessed tool.

Responses done events and terminal output contain snapshots. Only a missing
suffix is emitted; matching snapshots emit nothing. Conflicting snapshots fail
validation. Terminal snapshots can supply content when a compatible provider
omits deltas. Public text and refusal parts follow the same rule per content part.

Messages events are dispatched as SSE envelopes, not independent JSON lines.
`message_delta.stop_reason` does not replace `message_stop`: EOF between them is
an interrupted transport, not a committed assistant result. Unknown event types
and heartbeat comments are tolerated. Tool deltas need an identified tool block.

## Atomicity and failure contract

- Proposed tools are streamed for presentation; execution begins only after an
  entire response validates. Retries never execute partial JSON.
- Shared tool validation accepts only object arguments and nonempty call IDs and
  names; duplicate IDs are invalid in both streaming and ordinary responses.
- Chat `length`, Messages `max_tokens`, and Responses
  `incomplete_details.reason=max_output_tokens` reach the existing truncated-tool
  recovery path. Other Responses incomplete reasons remain response failures.
- Concrete HTTP status takes precedence over incidental error wording.
  Authentication/payment rejection is permanent; throttling, timeout/conflict
  statuses and server failures may retry. Explicit exhausted quota does not retry.
- In-stream provider errors retain an explicit retry classification; local parse
  errors remain `AIResponseError`, not invented connection failures.
- Cancellation is checked before opening Responses/Messages streams. Open streams
  close on successful completion, rejection, generator close and cancellation.
- Failed proposals are removed from the live transcript; executed tools retain
  their durable records. Clearing an abandoned assistant fragment does not erase
  canonical assistant history.

## Validation and limits

`tests/test_protocol_stream_contracts.py` exercises the actual adapters plus the
shared accumulator with deterministic streams. Existing provider, usage,
truncation, cancellation, reasoning and App Server suites remain in place.
SSE fixtures now include the blank-line event separator required by the protocol;
cache accounting expectations were not changed.

Coverage includes interleaved calls, fragmented function names (Chat), Unicode,
done/final snapshot duplication and contradiction, terminal-only snapshots,
invalid/nonobject JSON, duplicate identities, premature EOF, output limits,
permanent HTTP errors, exhausted quota, multiline SSE and transport cleanup.

No paid/live model requests are part of this verification. It cannot guarantee
every provider's undocumented behavior or network availability. New wire formats
should be captured in redacted adapter fixtures and validated at this boundary,
not repaired with task-text keywords or runtime monkeypatches.

Sources checked on 2026-10-08:
- [OpenAI Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)
- [Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
