# MCP server management

Settings → MCP supports adding, editing, enabling, disabling, reconnecting, and
deleting desktop-managed MCP servers. The server cards show actual connection
status, errors, and discovered tools. The external tool count excludes Loom's
two local MCP helper tools.

## Connections

- **HTTP:** use a Streamable HTTP endpoint over HTTPS, or HTTP on localhost.
  Authentication accepts a bearer token saved in the OS credential store or the
  name of an environment variable available to the Loom App Server.
- **Local process:** supply an executable, a JSON array of arguments, an optional
  working directory, and optional environment mappings. Mappings contain child
  variable names and parent variable names, never literal secret values. Install
  the executable separately; saving an enabled server starts that process.

GitHub, Context7, and Fetch shortcuts fill in editable configuration templates
and link to publisher documentation. They do not install dependencies or grant
account access. OAuth browser sign-in, SSE-only transports, and arbitrary HTTP
headers are not implemented in this panel.

## Marketplace

The Marketplace tab browses the Official MCP Registry REST API. Search matches
server names; load more to follow the registry's cursor. Filters apply to the
results already loaded. Details show the publisher namespace, exact version,
website/source links, installation methods, and compatibility with Loom.
Registry listings are publisher metadata, not a Loom endorsement.

Select a compatible method and fill its setup inputs, then choose **Review
configuration**. This generates a draft without installing or starting anything.
The next screen shows the actual URL or executable and argument array. Save to
connect through the existing MCP manager. Bearer tokens go into the OS credential
store; local package secrets are requested as parent environment variable names.
Required bearer authentication is checked before saving the generated draft.

Automatic plans support Streamable HTTP (including non-secret URL variables),
standard npm packages launched with `npx`, and standard PyPI packages launched
with `uvx`. Package versions are pinned to the version in the registry recipe.
Non-secret package arguments and environment references are collected in the
setup form. Missing launchers are reported before saving. Saving an enabled
local definition lets npx/uvx download and run that package.

SSE-only endpoints, arbitrary headers/authentication schemes, other package
formats, advanced runtime arguments, secret command arguments, fixed/templated
environment values, and recipes requiring additional checksum verification are
shown as manual setup, with reasons. Use publisher instructions and the manual
server editor for configurations the runtime supports.

Directory responses are cached for 15 minutes in `mcp-registry-cache` under the
runtime home, with at most 128 saved responses. Refresh bypasses the fresh cache.
If the registry is unavailable, saved results remain available with a stale-data
warning and fetch time. Without cached results, the page offers retry and the
existing quick/manual setup paths. Browsing and preparing a plan never send
service credentials to the directory.

## Persistence and activation

Definitions are stored in `mcp-servers.json` in the runtime home. Bearer tokens
are stored under the `loom-agent/mcp` OS keyring service; JSON stores only an
opaque reference. Existing tokens are never returned to the renderer. Leave
the token field empty to retain a credential, replace it to update it, or use
the remove-token action and save. If the credential store is unavailable, saving
a new token fails without writing a definition; environment references remain
available.

Changes are refused during active turns. When idle, saving reconnects the
configured services and applies the new tool binding to the next agent step.
Optional connection failures are saved and shown on the relevant card so they
can be corrected and retried. Failed file writes preserve the previous manager.
Definitions are loaded again at runtime startup. Environment changes require
restarting Loom so its App Server inherits them.

Servers loaded from existing runtime/third-party configuration remain visible
and read-only; edit their source file and restart Loom. Managed names cannot
override external server names or collide after tool-name normalization.
