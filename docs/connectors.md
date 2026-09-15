# Loom Connectors

Loom Connectors are the product-level account and authorization layer for external services. They sit **above** the generic MCP transport:

```text
User / Settings / CLI
        |
        v
Connector lifecycle
(discovery, auth, health, refresh, disconnect)
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

MCP remains the protocol for arbitrary tool servers. A Connector is for a service where Loom should provide a first-class product experience: connect an account, show its health, expose tools, preserve authorization identity, rotate credentials when supported, and disconnect cleanly without making users hand-edit `config.toml`.

## GitHub

GitHub is the first first-class Connector.

### Desktop

Open **Settings → Integrations → Connectors**. The GitHub card supports:

- browser/device sign-in;
- importing an already authenticated GitHub CLI session;
- entering a personal access token (PAT) into a password field;
- connection health/account metadata;
- device-OAuth token lifetime and automatic-rotation diagnostics;
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

For device OAuth, the access token and refresh token are both stored in the OS credential vault. Loom writes only non-secret expiry timestamps to `connector-oauth.json`; neither token is written there.

### Browser/device sign-in

For a packaged Loom distribution, configure the public GitHub application client ID with either the release-time `app/connector_product_config.py` value or an environment override:

```powershell
$env:LOOM_GITHUB_CLIENT_ID = "your_public_github_client_id"
```

The GitHub application must have Device Flow enabled. Loom sends the client ID and requested scopes to GitHub's device authorization endpoints and stores the resulting credential material in the OS credential vault. A client secret, private key, access token, or refresh token must never be baked into Loom source or the installer.

Scopes default to:

```text
repo read:org
```

Override them with `LOOM_GITHUB_OAUTH_SCOPES` if a deployment needs a narrower policy.

If `LOOM_GITHUB_CLIENT_ID` is not configured but GitHub CLI is installed, Loom falls back to `gh auth login --web --clipboard`. After the CLI completes browser authorization, Loom imports the resulting credential into its own keychain entry.

For development or environments without either route, paste a PAT in the desktop Connector page or use the CLI token command below.

### Expiring token rotation

GitHub may issue an expiring access token plus a refresh token for device/OAuth authorization. Loom treats that pair as one rotating authorization chain:

- the access token and refresh token stay only in the OS credential vault;
- `connector-oauth.json` stores only the access/refresh expiration timestamps;
- before a **future** model Step is sampled, Loom refreshes the access token inside a five-minute safety window when a valid refresh token is available;
- if the process restarts after the access token has expired, Loom can use the still-valid refresh token to recover the connection;
- GitHub token rotation produces a new binding identity for future Steps;
- already sampled Step routers continue using the exact old bound credential/client they captured, so refresh cannot retarget in-flight or approval-pending authority;
- an expired/invalid refresh token leaves the connector unavailable until the user reconnects.

Switching deliberately to a PAT or importing a GitHub CLI credential clears any stale Loom device-OAuth refresh chain. Explicit disconnect clears Loom's keychain access/refresh credentials and disables ambient auto-reconnection.

A deployment whose GitHub application does not issue refresh tokens still works; the Settings page reports that automatic rotation is unavailable and the user reconnects after that access token expires.

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

`refresh` checks credential health and performs a due OAuth rotation when the current device-flow access token is nearing expiration.

`logout` disables GitHub in Loom and clears Loom's keychain-backed connector credential chain. It does not mutate `GH_TOKEN`, `GITHUB_TOKEN`, or the user's GitHub CLI login. While the disabled marker is present, a refresh will not silently reconnect from ambient credentials. The user must explicitly reconnect or enable GitHub again.

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

When authorization changes or an expiring OAuth token rotates, Loom replaces only the long-lived registry projection used by **future** Steps. Already captured Step routers retain their old AgentTool objects and handler closures. This preserves the same rule Loom uses for exact MCP bindings: sampled execution authority cannot be retargeted by ambient live state.

## App Server RPC

The desktop uses two App Server methods:

### `connector/list`

Returns the current secret-free connector status list. Device-OAuth status can include non-secret lifetime fields such as whether automatic refresh is available and the remaining access/refresh lifetime.

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

## Packaging and release configuration

The Windows packaging work already freezes all `app` submodules and includes the Windows `keyring` backend, so the Connector and OAuth-refresh modules do not require a special PyInstaller hook when that packaging branch is composed with this work.

A self-contained installer cannot invent a GitHub application identity. Before a production Loom build promises one-click browser/device login, register the Loom GitHub application, enable Device Flow, and put only its **public client ID** into the release configuration. Keep client secrets/private keys out of the desktop binary.

The packaging workflow should smoke-test `import app` plus `RefreshingConnectorManager` after the connector and Windows-packaging branches are composed.

## Operational checks

Useful checks during development:

```text
loom connector github status
loom connector github refresh
loom connector list
```

Inside Loom, `github_connection_status` reports only non-secret metadata. MCP diagnostics continue to expose their separately discovered/connected server state.
