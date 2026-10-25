# Plugin architecture research

Research date: 2026-10-24. This document records the design basis for Loom's
declarative plugin bundles; it does not claim compatibility with every plugin
published for another host.

| Project / specification | Relevant design | Loom implementation |
| --- | --- | --- |
| [Claude Code plugin examples](https://github.com/anthropics/claude-code/tree/main/plugins) and [manifest reference](https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/skills/plugin-structure/references/manifest-reference.md) | A manifest groups skills, commands and MCP definitions; components use paths relative to the plugin root. | Read `.claude-plugin/plugin.json`, `skills/`, `commands/` and `.mcp.json`. Preserve package resources and resolve plugin-root placeholders inside the installed snapshot. |
| [Anthropic's public plugin directory](https://github.com/anthropics/claude-plugins-official) | Directory entries describe relative bundles or external Git sources, including subdirectories and pinned commits. | Browse and search the directory, acquire its source, then inspect the actual bundle before installation. Record the resolved Git revision and content digest. |
| [OpenCode plugin documentation](https://dev.opencode.ai/docs/plugins/) | JavaScript/TypeScript plugin functions register event hooks in the host's runtime. | This is a host-specific execution API. Loom reports unsupported hooks/modules rather than importing them into its Python runtime or claiming they are active. Executable tools use the existing MCP process boundary. |
| [Agent Skills specification](https://agentskills.io/specification) | Skill metadata and instructions live in `SKILL.md`; optional resources remain beside the skill. | Use the existing skill parser, search, load, resource and staging tools. Keep permission enforcement in Loom's existing runtime. |

## Design decisions

Plugins are declarative bundles of capabilities, not arbitrary modules imported
into the desktop process. Installation acquires and copies files without running
package-manager scripts or install hooks. Enabling starts the reviewed MCP
definitions and makes namespaced workflows discoverable.

The installed registry points to one content-addressed snapshot per plugin.
Preview tokens pin exactly the inspected files, avoiding source-folder edits
changing an approved installation. Activation verifies snapshot integrity.
Updates retain the old snapshot until the new registry and MCP binding are
ready; failed activation restores the old registry and credential references.

Skills use `plugin.<plugin>.<skill>` names, with a digest when shortening is
necessary. Commands are exposed as named skill workflows, not as a new shell or
Claude's slash-command interpreter. Explicit skill loading remains on demand.
Disabled, removed or replaced revisions stop contributing retained skill
context on the next request. A current revocation message supersedes historical
context, while historical conversation records stay intact.

Credentials belong to the OS keyring or server environment. Plugin manifests
contain environment references, and HTTP authentication supports bearer
references. A plugin cannot label an arbitrary executable tool as read-only to
avoid approval: generated MCP definitions retain Loom's sensitive-tool default.

Compatibility is explicit. Unsupported agents, hooks, host modules and settings
are shown during review; installing only supported components requires a separate
choice. Invalid paths, links, malformed definitions and literal credentials in
MCP environment/header declarations are rejected.

## Validation evidence

The public `code-review` package was acquired from the Anthropic directory and
recognized as a Claude-format bundle with one command workflow. Tests cover
real MCP stdio calls, skill discovery/loading, version replacement, retained
context revocation, restart recovery, credential isolation, rollback, ownership
of plugin MCP servers, archive/path validation and source preservation.
