# Loom Connectors

Loom Connectors are the product-level account and authorization layer for external services. They sit **above** the generic MCP transport:

```text
User / Settings / CLI
        |
        v
Connector lifecycle
(discovery, auth, health, disconnect)
        |
        v
Step-bound AgentTool snapshot
        |
        v
Permission / approval / sandbox runtime
        |
        v
External service API
```

MCP remains the protocol for arbitrary tool servers. A Connector is for a service where Loom should provide a first-class product experience: connect an account, show its health, expose tools, preserve authorization identity, and disconnect cleanly without making users hand-edit `config.toml`.

## GitHub

GitHub is the first first-class Connector.

### Desktop

Open **Settings → Integrations → Connectors**. The GitHub card supports:

- browser/device sign-in;
- importing an already authenticated GitHub CLI session;
- entering a personal access token (PAT) into a password field;
- connection health/account metadata;
- explicit disconnect.

Connector authorization cannot be changed while an Agent turn is active. This prevents a tool call sampled under one account from being executed under another account while an approval is pending.

### Credential resolution

When GitHub is enabled, Loom checks credentials in this order:

1. Loom's OS credential-vault entry (`loom-agent/connectors`, `github/access-token`);
2. `GH_TOKEN`;
3. `GITHUB_TOKEN`;
4. the token exposed by an authenticated `gh` CLI session.

Every candidate is validated against GitHub's `/user` endpoint before it becomes executable authority.

Tokens are never stored in `settings.json`, `connectors.json`, runtime status, notification payloads, tool results, or durable Step metadata. `connectors.json` contains only non-secret preferences (currently the enabled/disconnected marker and generation metadata).

### Browser/device sign-in

For a packaged Loom distribution, configure a GitHub OAuth App client ID as:

```powershell
$env:LOOM_GITHUB_CLIENT_ID = "your_oauth_app_client_id"
```

The OAuth App must have GitHub Device Flow enabled. Loom sends the client ID and requested scopes to GitHub's device authorization endpoints and stores the resulting access token in the OS credential vault.

Scopes default to:

```text
repo read:org
```

Override them with `LOOM_GITHUB_OAUTH_SCOPES` if a deployment needs a narrower policy.

If `LOOM_GITHUB_CLIENT_ID` is not configured but GitHub CLI is installed, Loom falls back to `gh auth login --web --clipboard`. After the CLI completes browser authorization, Loom imports the resulting credential into its own keychain entry.

For development or environments without either route, paste a PAT in the desktop Connector page or use the CLI token command below.

### CLI

```text
loom connector list
loom connector github status
loom connector github login
loom connector github import-gh
loom connector github token
loom connector github refresh
loom connector github logout
```

`loom connector github token` deliberately does **not** accept a token as a command-line argument. It reads a hidden terminal prompt (or stdin for automation), validates the token, then writes it to the OS credential vault. This keeps credentials out of normal process listings and shell history.

`logout` removes Loom's keychain credential and writes an explicit disabled marker. It does not mutate `GH_TOKEN`, `GITHUB_TOKEN`, or the user's GitHub CLI login. While that marker is disabled, a refresh will not silently reconnect from ambient credentials. The user must explicitly reconnect or enable GitHub again.

## GitHub Agent tools

GitHub tools use fixed `api.github.com` endpoints; model arguments never supply an arbitrary network host.

Read-only tools:

- `github_connection_status`
- `github_repository_get`
- `github_repository_list`
- `github_file_read`
- `github_code_search`
- `github_issue_list`
- `github_pull_request_list`
- `github_pull_request_get`
- `github_workflow_runs_list`

External-write tools (classified `SENSITIVE`):

- `github_issue_create`
- `github_issue_comment`
- `github_pull_request_create`
- `github_branch_create`
- `github_file_write`

Sensitive tools continue through Loom's normal `PermissionEngine` / approval boundary. Connecting GitHub never grants the model a bypass around approval policy.

Most GitHub tools are `DEFERRED` so Tool Search can expose them on demand without inflating every model request. Connection status is direct so the model can explain why GitHub is unavailable.

## Step-bound authorization

GitHub AgentTool instances capture the exact validated credential binding that existed when they were created. The binding identifier is a process-local HMAC projection; it is not the credential itself.

When authorization changes, Loom replaces only the long-lived registry projection used by **future** Steps. Already captured Step routers retain their old AgentTool objects. This preserves the same rule Loom uses for exact MCP bindings: sampled execution authority cannot be retargeted by ambient live state.

## App Server RPC

The desktop uses two App Server methods:

### `connector/list`

Returns the current secret-free connector status list.

### `connector/manage`

Parameters:

```json
{
  "provider": "github",
  "action": "status | refresh | enable | start_auth | poll_auth | import_gh | connect_token | disconnect"
}
```

`connect_token` includes a transient `token` field. `poll_auth` includes `sessionId` returned by `start_auth`.

The App Server advertises the Connector surface in `initialize.capabilities.connectors` and emits `connector/updated` plus `runtime/updated` after an authorization change.

## Codex MCP configuration reuse

Loom also discovers MCP servers already configured by Codex at:

```text
$CODEX_HOME/config.toml
~/.codex/config.toml
```

The Codex config is considered only when it actually contains a non-empty `[mcp_servers]` table. Loom's own explicit configuration remains higher priority; Codex discovery then takes precedence over opportunistic Claude Desktop / Cursor adoption.

Supported Codex mappings include:

- stdio `command`, `args`, and `cwd`;
- `env_vars` (forwarded from the current process by name);
- `env` values expressed as `$NAME` or `${NAME}` references;
- streamable HTTP `url`;
- `bearer_token_env_var`;
- startup/tool timeout values.

Loom intentionally refuses to import literal `env` values or literal HTTP headers from another application's configuration. It also refuses `env_http_headers` until Loom's MCP transport has an equivalent typed header-environment contract. A refused Codex candidate is reported in MCP diagnostic status and discovery continues to the next candidate rather than weakening the secret boundary.

## Operational checks

Useful checks during development:

```text
loom connector github status
loom connector list
```

Inside Loom, `github_connection_status` reports only non-secret metadata. MCP diagnostics continue to expose their separately discovered/connected server state.
