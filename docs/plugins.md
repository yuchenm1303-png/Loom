# Loom plugins

Plugins bundle reusable Skills, command workflows and MCP server definitions.
Open Settings → Plugins to discover public bundles, inspect a local folder/ZIP
or HTTPS Git source, or preview the included `review-assistant` example.

## Install and manage

1. Inspect the package. Review its source, resolved Git revision, content digest,
   workflow names and MCP launch definitions.
2. Fill any credentials in the password fields, or leave them empty to use an
   existing credential or an App Server environment variable.
3. If a bundle has unsupported components, decide whether to install only the
   supported subset. Otherwise installation stays blocked.
4. Install with activation enabled, or install disabled and enable it later.

Enabling may start MCP processes or connect to remote services. Installation
does not run post-install scripts or execute bundled workflows. Workflows are
discovered with `skill_search` and loaded with `skill_load`; bundled scripts
still require normal tool permissions and sandbox access.

The installed cards support enabling, disabling, reconnecting, updating,
credential changes and uninstalling. Updates are inspected before application.
Directory-installed plugins recheck their directory source when reviewing an
update; explicitly pinned Git sources remain pinned. Changes are refused during
active turns and apply to subsequent requests when idle.

`Active` means the supported components are available. `Needs attention` means
one or more MCP services did not connect; expand the card for errors, configure
credentials or install the required launcher/dependencies, then reconnect.
Old registration-only entries show `Needs reinstall` until a real package is
inspected and installed. They never execute automatically.

## Bundle format

```text
review-tools/
  loom-plugin.json
  skills/
    review-change/
      SKILL.md
      references/
  commands/                 # optional Markdown workflows
  .mcp.json                 # optional MCP definitions
  scripts/                  # optional files; not automatically executed
```

```json
{
  "schemaVersion": 1,
  "name": "review-tools",
  "version": "1.0.0",
  "description": "Review changes with project-specific tools",
  "skills": ["./skills"],
  "commands": ["./commands"],
  "mcpServers": "./.mcp.json"
}
```

Only declare optional paths that exist. Without explicit paths, existing
`skills/`, `commands/` and `.mcp.json` are discovered. Collections of valid
`SKILL.md` files can also be installed without a plugin manifest. Plugin names
contain 1–128 letters, digits, dots, underscores or hyphens.

Claude-style `.claude-plugin/plugin.json` bundles are supported for these same
components. For those manifests, explicit skill/command paths supplement default
directories. Commands require YAML frontmatter with a description and are loaded
as workflows; Claude-specific command arguments, agents, callbacks, hooks, LSP
servers, output styles and host settings are not implemented.

Example `.mcp.json`:

```json
{
  "mcpServers": {
    "project-tools": {
      "command": "python",
      "args": ["${LOOM_PLUGIN_ROOT}/scripts/server.py"],
      "env": {"SERVICE_API_KEY": "${SERVICE_API_KEY}"}
    },
    "remote-tools": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${REMOTE_TOKEN}"}
    }
  }
}
```

`${CLAUDE_PLUGIN_ROOT}` is accepted as an alias for `${LOOM_PLUGIN_ROOT}`. Local
MCP processes default to the installed package as their working directory.
Only plugin-root placeholders are expanded in launch paths/arguments; use
environment references for secrets. Native `env_from` and `bearer_token_env`
references are also accepted. Literal environment values, custom HTTP headers
and unsupported transport/settings fields are rejected with an explanation.
Install external executables such as Python, Node.js, uv or Docker separately.

## Ownership, storage and recovery

Plugin MCP names are namespaced and appear read-only in MCP settings, with their
owning plugin identified. Change them through Plugins. Manual MCP definitions,
user Skills and repository Skills remain independently managed.

Plugin files live under `<runtime-home>/plugins/packages/<content-sha256>`;
`plugins.json` selects the installed revisions. Previews expire after 30 minutes
and are limited to 20 pending packages. Bundles are limited to 2048 files / 64 MB.
Links, ZIP traversal paths and unsafe Windows archive names are rejected.

The source folder is never modified or deleted. Uninstall removes the managed
snapshot and the plugin's credential references, and deletes its owned keyring
entries. Disabling keeps the installed package and credentials for later use.
Failed activation restores the old registry and keyring values. Corrupt or
missing plugins are isolated so other plugins and the application can start.

The public directory is cached for 15 minutes and falls back to saved metadata
with a warning when offline. Directory listings are not a guarantee of Loom
compatibility. Package preview is the compatibility check.

See [the research notes](plugin-research.md) for source projects and rationale.
