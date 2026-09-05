# MCP Runtime v1

Loom can load Model Context Protocol servers as first-class runtime tools. MCP tools still cross Loom's own `ToolRegistry`, `ToolOrchestrator`, permission profiles, approval flow, cancellation, result-size limits, and durable event boundaries.

## Install the MCP adapter

```bash
pip install -e '.[mcp]'
```

The base Loom install does not require the MCP SDK. If no MCP servers are configured, MCP remains disabled and the normal Runtime stack is unchanged.

## Configuration

The default CLI/runtime reads `$LOOM_CONFIG` when set, otherwise `<LOOM_HOME>/config.toml` (normally `~/.loom/config.toml`). A missing file simply means no MCP servers are enabled.

### stdio server

```toml
[mcp_servers.github]
transport = "stdio"
command = "npx"
args = ["-y", "example-mcp-server"]
default_effect = "sensitive"

[mcp_servers.github.env_from]
GITHUB_TOKEN = "LOOM_GITHUB_TOKEN"

[mcp_servers.github.tool_effects]
search_issues = "read_only"
```

`env_from` maps the child process variable name to a variable in Loom's parent environment. Secret values are not stored in the TOML file. The MCP SDK itself starts stdio children with a minimal inherited environment; Loom only adds variables explicitly named in `env_from`.

### Streamable HTTP server

```toml
[mcp_servers.docs]
transport = "http"
url = "https://mcp.example.com/mcp"
bearer_token_env = "LOOM_DOCS_MCP_TOKEN"
required = true
```

Remote plaintext HTTP is rejected. HTTPS is required except for `localhost` and `127.0.0.1`. Bearer tokens are read from the named environment variable at runtime and are never copied into Loom Session state or tool results.

## Permission model

MCP tools default to `sensitive`. This is intentional: an unknown remote tool may read or mutate an external system, so Loom does not infer safety from the tool name or description.

For servers you trust and understand, individual tools can be downgraded explicitly:

```toml
[mcp_servers.github.tool_effects]
search_issues = "read_only"
get_issue = "read_only"
create_issue = "sensitive"
```

Valid effects are `read_only`, `mutating`, and `sensitive`. The existing Loom permission profiles decide whether the call is allowed, denied, or requires user approval.

## Tool names and exposure

Remote tools are registered as:

```text
mcp.<server>.<tool>
```

Names are canonicalized to Loom's model-safe tool-name contract. The lower-level `MCPRuntime` preserves each server tool's configured exposure. The default top-level Loom runtime now adds Tool Search and automatically moves MCP tools that would otherwise be `direct` to `deferred`, keeping large MCP catalogs out of the initial model context.

The model initially sees the read-only `tool_search` tool. A matching MCP tool is exposed only after the model searches for the needed capability, and that activation lasts only for the current turn. `hidden` and `code_mode_only` tools are never returned by Tool Search. Embedders that intentionally want the old direct MCP surface can instantiate the lower-level `MCPRuntime` or set `defer_mcp_tools=False` on `ToolSearchRuntime`.

## Durable-state boundary

MCP text and structured results are bounded before they reach the model/session. Secret-shaped text is redacted. Image/audio payload bytes are not embedded into durable tool results; v1 records a bounded placeholder instead. File/resource transfer will get a workspace-bound policy in a later stage rather than silently persisting arbitrary remote binary data.

Deferred-tool activation itself is intentionally turn-scoped and is not written into long-lived Session state. If a deferred sensitive tool has already reached Loom's approval boundary, the durable pending-approval record contains the exact tool name. A fresh runtime can therefore reconstruct that one tool for `resume_approval` without persisting the broader search result set.

## Protocol/SDK baseline

Loom MCP Runtime v1 targets the official Python MCP SDK `2.x` and the modern stateless MCP protocol path. It supports stdio and Streamable HTTP; legacy SSE is intentionally not added as a new first-class transport.
