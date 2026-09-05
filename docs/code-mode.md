# Code Mode v1

Loom Code Mode lets the model compose several Loom tool calls inside one bounded code cell, reducing model/tool round trips for multi-step mechanical work.

The design follows the same core separation used by current Codex Code Mode: code execution is a separate capability surface, while nested tool calls are dispatched back through the core tool runtime instead of receiving direct host capabilities.

## Model tool

The default `CodeModeRuntime` exposes a direct read-only meta-tool named `code_mode`:

```json
{
  "code": "a = tools.search_workspace_text(query='TODO')\nemit(a['data']['matches'])"
}
```

Inside a cell, Loom tools can be called in two forms:

```python
result = tools.search_workspace_text(query="TODO", path=".")
emit(result)
```

or, for names that are not convenient Python attribute paths:

```python
result = tool("mcp.github.some_tool", {"query": "Loom"})
emit(result)
```

A nested result is a JSON object containing at least `ok`, `content`, and `data`.

## Restricted language

Code Mode v1 is deliberately not arbitrary Python. Loom parses the source with Python's `ast` module and interprets only an allowlisted JSON-oriented subset. It does not call Python `eval`, `exec`, or `compile` on model source.

Supported constructs include:

- simple variable assignment and augmented assignment
- `if`
- bounded `for` loops with `break` / `continue`
- strings, numbers, booleans, null, lists and dictionaries
- indexing and slicing
- comparisons, boolean expressions and bounded arithmetic
- safe helpers such as `len`, `range`, `get`, `keys`, `values`, `items`, `sorted`, `min`, `max`, `sum`, and `json`
- `emit(value)` / `print(value)` for model-visible output
- `tools.<name>(...)` and `tool(name, ...)` for nested Loom tools

Explicitly unavailable:

- imports
- function, lambda or class definitions
- `while`, `try`, `with`, generators and comprehensions
- arbitrary Python object attributes or methods
- Python file APIs
- subprocess / shell APIs
- sockets and network libraries
- `eval`, `exec`, reflection and dynamic imports

Code Mode therefore does not claim to be an OS sandbox. It avoids giving model source a general Python execution capability in the first place.

## Tool exposure

The nested surface follows Loom tool exposure rules:

- `DIRECT`: directly model-visible and callable from Code Mode.
- `CODE_MODE_ONLY`: hidden from the normal model tool list but callable from Code Mode.
- `DEFERRED`: callable from Code Mode only after activation for the current turn through `tool_search`.
- `HIDDEN`: never callable from Code Mode.

`code_mode` itself is excluded from the nested surface to prevent recursive code cells.

## Permission boundary

Every nested invocation is reconstructed as a Loom `ToolCall`, validated against its tool schema, and passed through the existing `ToolOrchestrator` and `PermissionEngine`.

A nested tool is executed only when the current permission profile returns `ALLOW`.

- `DENY`: the nested result reports that permissions blocked the call.
- `APPROVAL`: Code Mode v1 does **not** silently elevate or batch-approve the call. The nested result returns `requires_approval=true` and the tool is not executed. The model must call that tool normally outside Code Mode so Loom can enter the durable user approval flow.

This prevents a single Code Mode invocation from becoming a blanket approval for arbitrary nested mutations or sensitive operations.

## Durability and history

The model receives one normal `code_mode` tool result for the whole cell. Nested calls do not add unmatched tool-role messages to the durable model conversation, which keeps tool-call history valid.

Nested activity is still emitted as normal runtime events with `nested=true` and a `parent_call_id`, so the Web UI and diagnostics can show what Code Mode is doing.

Nested calls count against the turn's normal tool-call budget in addition to Code Mode's own cell limits.

## Current v1 limitations

- No suspended/resumable code cell across interactive approval. Approval-required calls must be retried normally outside the cell.
- No arbitrary third-party Python packages in cells.
- No persistent variables across separate `code_mode` calls.
- No parallel nested tool calls yet.
- No true token streaming is provided by Code Mode; UI streaming is a separate provider/runtime concern.
