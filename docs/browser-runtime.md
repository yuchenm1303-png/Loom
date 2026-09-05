# Loom Browser Runtime v1

Loom's browser layer is a Runtime capability, not a collection of ad-hoc Playwright calls. The ownership boundary is:

`BrowserRuntime -> BrowserSessionStore -> BrowserSessionHandle -> BrowserStateSnapshot -> BrowserSecurityPolicy -> Loom Browser Tools`

The first backend adapter uses `browser-use`'s `BrowserSession` behind Loom's own narrow `BrowserBackend` protocol. `browser-use` is optional rather than a core dependency: its current package includes its own agent/LLM/MCP/document stack, while Loom already owns those layers. Keeping it behind an adapter lets Loom reuse its mature CDP/DOM/watchdog implementation now and replace the backend later without changing Loom's tool or permission contract.

Install browser support with:

```bash
pip install -e ".[browser]"
```

`browser-use` currently controls Chromium primarily through CDP (`cdp-use`). Loom does not route Browser actions around Runtime v2: every model-facing Browser tool is still registered in `ToolRegistry`, resolved through `ToolRouter`, and gated by `ToolOrchestrator`/permission profiles. `browser_status` is read-only; all browser network/read/interaction tools are `SENSITIVE`, so read-only denies them, approval/workspace request approval, and full-access can execute them.

## Browser state and element identity

Browser state is deliberately LLM-facing and bounded; Loom never dumps a raw browser process or unbounded DOM object into canonical runtime state. The backend uses the serialized `BrowserStateSummary` produced by browser-use's DOM layer.

browser-use's integer selector indexes represent the current serialized DOM; they are not permanent cross-step element IDs. Loom v1 therefore attaches a monotonically increasing `state_revision` to each model-visible state and caches the exact selector map that produced it. `browser_click` and `browser_type` require that revision. If state has refreshed, the old revision is rejected rather than silently resolving the same integer against a different DOM.

## Navigation security

Loom performs URL policy checks before explicit navigation and after browser actions. `BrowserSecurityPolicy` canonicalizes percent-encoded and Unicode hostnames, detects normal/decimal/hex/octal IPv4 spellings accepted by platform resolvers, blocks userinfo credentials in URLs, rejects localhost/prohibited hosts, resolves DNS by default, and blocks non-global addresses unless explicitly configured otherwise.

The backend independently enables browser-use's `SecurityWatchdog`, so redirects and popup/new-tab navigations are checked at the browser execution layer as well. Per-session `allowed_domains` restrictions are passed to the backend. If Loom observes a prohibited final page/tab despite backend enforcement, the whole managed browser session is torn down.

This is not described as an OS/network sandbox. DNS rebinding and Chromium process isolation are separate concerns from URL policy. Loom's existing Bubblewrap sandbox does not magically contain a browser process launched outside the managed exec path.

## Secret boundary

Browser v1 intentionally has **no automatic credential/sensitive-data injection channel**. API keys, tokens, passwords, cookies, private keys, storage state, and browser cookies are not copied from Loom configuration into `BrowserSession`, `WorldState`, SQLite, Session, ToolResult, or logs by the Browser layer.

Model-visible browser text and URLs receive defense-in-depth redaction for common secret-shaped assignments, bearer/OpenAI/provider tokens, JWTs, and sensitive query parameters. `browser_type` never echoes typed text in its result. This does not make arbitrary page content trustworthy; prompt injection remains untrusted input.

Screenshots are only produced on an explicit `browser_screenshot` tool call. PNG bytes are written to a path inside the current Loom workspace; screenshot bytes are not embedded in ToolResult or Session history.

## Lifecycle and honest limitations

Browser handles are ephemeral in-memory Runtime resources. `BrowserRuntime.close()` closes all owned browser resources, permission transitions close that Loom session's browsers, and interrupted recovery discards its browser handles. Browser v1 does **not** persist cookies/storage state, does not reattach to a prior browser after a Loom process restart, and does not claim durable browser crash recovery.

Downloads and uploads are intentionally deferred in v1. The backend profile can support them, but exposing files safely requires a separate workspace-bound file-transfer policy. Until that exists Loom does not expose model-facing browser upload/download tools.

The initial model-facing tools are:

- `browser_status`
- `browser_open`
- `browser_state`
- `browser_navigate`
- `browser_click`
- `browser_type`
- `browser_scroll`
- `browser_back`
- `browser_refresh`
- `browser_tabs`
- `browser_switch_tab`
- `browser_close_tab`
- `browser_screenshot`
- `browser_close`
