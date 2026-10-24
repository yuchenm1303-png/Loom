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
