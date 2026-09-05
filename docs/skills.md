# Skills Runtime v1

Loom supports reusable Codex-compatible `SKILL.md` workflows through the default `SkillRuntime` layer.

## Discovery

For a session workspace inside a Git repository, Loom searches `.agents/skills` from the workspace directory upward to the nearest repository root, then checks user roots. Nearest repository scope wins when multiple skills share the same name.

Default user roots are:

- `<LOOM_HOME>/skills`
- `~/.agents/skills`

Each skill is a directory containing `SKILL.md` with YAML frontmatter:

```markdown
---
name: release-check
description: Verify a release before publishing
short_description: Release verification
---

Run the repository checks, inspect the diff, and report blockers before publishing.
```

`name` may contain letters, numbers, dots, underscores and hyphens. `description` is required.

## On-demand exposure

Skill bodies are not placed into the initial model context. Loom exposes two read-only tools:

- `skill_search`: searches skill metadata for the current workspace.
- `skill_load`: loads one exact skill body after discovery.

A typical model flow is:

1. Search for a relevant skill.
2. Load the selected skill by exact name.
3. Follow its workflow using normal Loom tools.

The body only becomes visible to the model after `skill_load` succeeds.

## Security boundary

Skills are instructions, not privileged code. They do not bypass Loom's `PermissionEngine`, tool exposure rules, browser policy, sandbox policy, MCP policy, or approval flow.

Loom also applies these discovery boundaries:

- Hidden directories below a skill root are skipped.
- Resolved `SKILL.md` paths must remain inside the discovery root, preventing symlink escape.
- Skill files are size bounded.
- Skill instructions are bounded and secret-shaped values are redacted before being returned to the model.
- Duplicate names use deterministic first-root-wins precedence.

Do not store real credentials in `SKILL.md`. Redaction is a defense-in-depth measure, not a secret vault.

## Current scope

Skills Runtime v1 intentionally does not execute arbitrary scripts from a skill directory and does not grant extra permissions. Additional assets may be referenced by future versions only through explicit Loom tool boundaries.
