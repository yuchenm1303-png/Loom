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

### Desktop experience

Open **Settings → Integrations → Connectors**. The primary GitHub flow is intentionally ordinary desktop OAuth:

1. click **Connect GitHub**;
2. Loom opens GitHub in the default browser;
3. approve Loom on GitHub;
4. GitHub redirects to a temporary `http://127.0.0.1:<random-port>/oauth/github/callback` listener owned by the running Loom process;
5. Loom verifies the OAuth `state`, exchanges the authorization code with PKCE, validates the returned token against `/user`, stores the credential in the OS keychain, and closes the local callback listener;
6. the Settings page notices completion automatically.

The normal Web OAuth path never asks the user to copy a device code, run `gh auth login`, paste a PAT, or manually return a token to Loom.

GitHub CLI import, Device Flow, and PAT import remain **advanced/recovery fallbacks** for development, headless machines, managed environments, or builds that have not been provisioned with the Loom OAuth application identity.

Connector authorization cannot be changed while an Agent turn is active. This prevents a tool call sampled under one account from being executed under another account while an approval is pending.

### One-click browser OAuth

GitHub supports loopback redirect URLs for native desktop applications. Register the Loom OAuth App with this callback URL:

```text
http://127.0.0.1/oauth/github/callback
```

At runtime Loom binds `127.0.0.1` to an available ephemeral port and supplies, for example:

```text
http://127.0.0.1:49321/oauth/github/callback
```

GitHub explicitly permits the loopback port to differ from the registered callback port. Use the literal loopback address rather than `localhost`.

Configure the desktop distribution with:

```powershell
$env:LOOM_GITHUB_CLIENT_ID = "your_github_oauth_client_id"
$env:LOOM_GITHUB_CLIENT_SECRET = "your_github_oauth_client_secret"
$env:LOOM_GITHUB_OAUTH_SCOPES = "repo read:org"
$env:LOOM_GITHUB_CALLBACK_PATH = "/oauth/github/callback"
```

The same values can be supplied through the release-time constants in `app/connector_product_config.py`. Environment values win over baked defaults.

GitHub's Web Application Flow currently requires `client_secret` when exchanging an authorization code, even when PKCE is used. Loom is a native/public client, so an embedded OAuth client credential cannot be treated as confidential: anyone able to inspect the desktop binary can recover application material. It is therefore **not an authorization boundary**. The real desktop protections are:

- PKCE with `S256` (`code_challenge` / `code_verifier`);
- an unguessable per-attempt OAuth `state` checked with constant-time comparison;
- a callback listener bound only to `127.0.0.1` on an ephemeral port;
- a ten-minute authorization lifetime;
- immediate callback-listener shutdown after success/failure;
- validation of every returned access token through GitHub `/user` before it becomes executable authority;
- OS-keychain storage for user access/refresh tokens.

Never commit or package **user** access tokens, refresh tokens, PATs, GitHub App private keys, or any other user-specific credential. If a future Loom distribution needs a genuinely confidential application secret, move the code exchange behind a Loom-operated HTTPS OAuth relay; the desktop flow and PKCE contract can remain the same.

### Credential resolution

When GitHub is enabled, Loom checks credentials in this order:

1. Loom's OS credential-vault entry (`loom-agent/connectors`, `github/access-token`);
2. `GH_TOKEN`;
3. `GITHUB_TOKEN`;
4. the token exposed by an authenticated `gh` CLI session.

Every candidate is validated against GitHub's `/user` endpoint before it becomes executable authority.

Tokens are never stored in `settings.json`, `connectors.json`, runtime status, notification payloads, tool results, or durable Step metadata. `connectors.json` contains only non-secret preferences (currently the enabled/disconnected marker and generation metadata).

For OAuth flows, access and refresh tokens are stored in the OS credential vault. Loom writes only non-secret authorization kind and expiry timestamps to `connector-oauth.json`; neither token is written there.

### Browser fallback behavior

`WebOAuthConnectorManager` selects the login path in this order:

1. **Web OAuth + PKCE + loopback callback** when both `LOOM_GITHUB_CLIENT_ID` and `LOOM_GITHUB_CLIENT_SECRET` are configured;
2. **Device Flow** when a client ID exists but the Web OAuth credential pair is incomplete;
3. **GitHub CLI browser login** when `gh` is installed and no first-party OAuth application identity is configured;
4. **PAT import** remains available manually from Advanced / recovery settings.

Device Flow is no longer the intended desktop product experience. It is retained because it is useful for constrained/headless environments and for safe recovery before a release is provisioned with its OAuth App credentials.

### Expiring token rotation

GitHub may issue an expiring access token plus a refresh token for OAuth authorization. Loom treats that pair as one rotating authorization chain:

- the access token and refresh token stay only in the OS credential vault;
- `connector-oauth.json` stores only the OAuth kind and access/refresh expiration timestamps;
- before a **future** model Step is sampled, Loom refreshes the access token inside a five-minute safety window when a valid refresh token is available;
- Web OAuth refresh requests include the OAuth App client secret, as required by GitHub; Device Flow refresh does not require that secret;
- if the process restarts after the access token has expired, Loom can use the still-valid refresh token to recover the connection;
- GitHub token rotation produces a new binding identity for future Steps;
- already sampled Step routers continue using the exact old bound credential/client they captured, so refresh cannot retarget in-flight or approval-pending authority;
- an expired/invalid refresh token leaves the connector unavailable until the user reconnects.

Switching deliberately to a PAT or importing a GitHub CLI credential clears any stale Loom OAuth refresh chain **only after** the replacement credential has been validated and committed. A failed PAT or failed GitHub CLI import leaves the existing working OAuth chain untouched. Explicit disconnect clears Loom's keychain access/refresh credentials and disables ambient auto-reconnection.

A deployment whose GitHub OAuth application does not issue refresh tokens still works; the Settings page reports that automatic rotation is unavailable and the user reconnects if the access token later expires.

### Cross-agent credential synchronization

Connector authority remains immutable within a sampled model Step, but long-lived agents do not stay disconnected forever when another Loom process changes the shared keychain or the user logs in through `gh`.

At future Step boundaries, a disconnected manager periodically re-probes the OS keychain, `GH_TOKEN`, `GITHUB_TOKEN`, and GitHub CLI even when `connectors.json` itself did not change. `github_connection_status` also performs an explicit live probe. A newly discovered credential updates only the long-lived tool registry for the **next** Step; GitHub data/write handlers already captured by the current Step remain bound to their original authority.

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

`loom connector github login` uses the same manager selection as the desktop: Web OAuth is preferred when the OAuth App is fully configured, with Device Flow / GitHub CLI retained as fallback paths.

`loom connector github token` deliberately does **not** accept a token as a command-line argument. It reads a hidden terminal prompt (or stdin for automation), validates the token, then writes it to the OS credential vault. This keeps credentials out of normal process listings and shell history.

`refresh` checks credential health and performs a due OAuth rotation when the current access token is nearing expiration.

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

Most GitHub tools are `DEFERRED` so Tool Search can expose them on demand without inflating every model request. Connection status is direct so the model can explain why GitHub is unavailable. The connector test suite includes a real `ToolSearchRuntime` contract where the model first sees only `tool_search`, activates `github_issue_create`, and then executes it through the bound GitHub client.

## Step-bound authorization and provenance

GitHub AgentTool instances capture the exact validated credential binding that existed when they were created. The binding identifier is a process-local HMAC projection; it is not the credential itself.

When authorization changes or an expiring OAuth token rotates, Loom replaces only the long-lived registry projection used by **future** Steps. Already captured Step routers retain their old AgentTool objects and handler closures. This preserves the same rule Loom uses for exact MCP bindings: sampled execution authority cannot be retargeted by ambient live state.

For auditability, `RequestStateSnapshot.connector_binding_json` freezes a secret-free connector projection at the same semantic Step boundary. It contains only Loom-verifiable fields such as `connector_id`, connected/enabled state, account, credential source, binding ID, and scopes. It participates in the request-state digest. Exact authority remains in the captured AgentTool handler; the JSON is diagnostic/integrity metadata only.

Loom does **not** synthesize Codex `plugin_id`, `connector_id`, or `link_id` provenance that it cannot prove. The GitHub Connector records Loom's own trusted connector identity rather than fabricating upstream identifiers.

## App Server RPC

The desktop uses two App Server methods:

### `connector/list`

Returns the current secret-free connector status list. OAuth status can include non-secret lifetime fields such as whether automatic refresh is available, remaining access/refresh lifetime, whether one-click Web OAuth is provisioned, and the preferred browser login mode.

### `connector/manage`

Parameters:

```json
{
  "provider": "github",
  "action": "status | refresh | enable | start_auth | poll_auth | import_gh | connect_token | disconnect"
}
```

`connect_token` includes a transient `token` field. `poll_auth` includes `sessionId` returned by `start_auth`. `start_auth` opens the system browser automatically when a browser target is available. Web OAuth authorization responses expose only non-secret metadata such as `sessionId`, mode, status, redirect URL, and poll interval; OAuth state, PKCE verifier, authorization code, and token responses remain backend-only.

The App Server advertises the Connector surface in `initialize.capabilities.connectors`, including `loopbackOAuth` and `pkce`, and emits `connector/updated` plus `runtime/updated` after an authorization change.

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

The Windows packaging workflow on `main` freezes all `app` submodules and includes the Windows `keyring` backend, so `connector_web_oauth.py` is included automatically by the existing `--collect-submodules app` build contract.

A self-contained installer cannot invent a GitHub OAuth application identity. Before a production Loom build promises one-click browser login, register the Loom OAuth App, configure the loopback callback shown above, and provide the native-client credential pair at release time. Device Flow does not need to be enabled for the normal desktop experience; enable it only if that release intentionally wants the constrained-device fallback.

The frozen-runtime Windows smoke should instantiate the same connector manager used by the app-server so import/Windows-keyring regressions are detected in the packaged executable.

## Operational checks

Useful checks during development:

```text
loom connector github status
loom connector github refresh
loom connector list
```

Inside Loom, `github_connection_status` reports only non-secret metadata. MCP diagnostics continue to expose their separately discovered/connected server state.
