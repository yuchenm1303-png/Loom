# Tool response boundary

All registered chat backends enter `normalize_response`, including streaming
and non-streaming Chat Completions, Responses and Messages transports. Provider
names and model IDs do not select fallback behavior.

Structured calls are canonical. When an endpoint leaks a complete DSML wire
envelope into assistant content, the syntax codec converts it atomically into
the same `ToolCall` objects. Parameter strings remain literal; non-string
parameters must contain valid JSON. Both forms validate tool names, unique IDs,
request exposure and input schemas. Mixed structured/text calls, incomplete
envelopes, duplicate parameters and truncated calls are rejected before dispatch.
Normal runtime permissions, approvals and tool-result history still apply.

The stream boundary holds candidate protocol tags across chunk boundaries so
wire arguments are not published as conversation text. Canonical history is
committed only after full parsing. Unsupported textual wire formats and missing
calls with a tool finish reason fail through the existing bounded response
recovery path. Repeated endpoint failures remain explicit failures, not successful
answers. No parser guesses commands from prose or generic JSON/code examples.

Tests cover every split position, byte-independent Unicode text, native/text
collisions, arbitrary model identities, streaming/non-streaming integration,
undeclared tools, schema violations, truncation and normal text preservation.
Unknown future formats require a syntax codec or upstream native tool support;
claiming compatibility without that evidence would be misleading.
