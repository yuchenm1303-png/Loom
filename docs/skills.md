# Skills Runtime v2

Loom supports reusable Agent Skills bundles built around Codex-compatible `SKILL.md` files. Skills can be discovered from a repository, installed into the Loom user home, searched and loaded on demand, and can include scripts, references, templates, and binary assets.

## Install and manage skills

The `loom` executable now has a dedicated `skill` command family. These commands do not require an AI model or API key.

```bash
loom skill install ./my-skill
loom skill install ./my-skill.zip
loom skill install https://example.com/my-skill.zip
loom skill install https://github.com/owner/repository
loom skill install https://github.com/owner/repository/tree/main/path/to/skill

loom skill list
loom skill search pdf
loom skill info pdf-helper
loom skill update pdf-helper
loom skill update --all
loom skill remove pdf-helper
```

A source containing exactly one `SKILL.md` is installed automatically. For repositories that contain many skills, select one or install all discovered bundles:

```bash
loom skill install https://github.com/owner/skills --name pdf-helper
loom skill install https://github.com/owner/skills --all
```

Use `--force` to replace an installed skill during a manual install. Loom records installation provenance in `.loom-skill.json`, which lets `loom skill update` reacquire the original source.

Installed user skills live under:

- `<LOOM_HOME>/skills`

The legacy-compatible user root remains supported:

- `~/.agents/skills`

## Discovery

For a session workspace inside a Git repository, Loom searches `.agents/skills` from the workspace directory upward to the nearest repository root, then checks user roots. Nearest repository scope wins when multiple skills share the same name.

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

Skill bodies are not placed into the initial model context. Loom exposes four skill tools:

- `skill_search`: search skill metadata for the current workspace.
- `skill_load`: load one exact `SKILL.md` body after discovery.
- `skill_read_resource`: read a UTF-8 text file bundled with a skill.
- `skill_stage_bundle`: copy the complete skill bundle into `.loom/skill-runs/<name>` in the current workspace.

A typical model flow is:

1. Search for a relevant skill.
2. Load the selected skill by exact name.
3. Read references/templates directly when needed.
4. If the workflow needs bundled scripts or binary assets, stage the bundle into the workspace.
5. Run scripts with Loom's normal process tools.

Staging itself never executes code.

## Bundle support

An installed skill may contain files such as:

```text
my-skill/
├── SKILL.md
├── scripts/
│   └── verify.py
├── references/
│   └── policy.md
├── templates/
│   └── report.md
└── assets/
    └── example.png
```

Text resources can be read without copying the bundle into the project. Scripts and binary assets can be staged into the current workspace so existing Sandbox and Process Runtime rules continue to apply.

## Installation safety

Installing a skill is deliberately inert. Loom does not execute package-manager hooks, shell commands, Python files, JavaScript files, or dependency installers while installing or updating a skill.

The installer also applies basic supply-chain boundaries:

- Remote sources must use `https://` by default; `ssh://`, `git://`, and `http://` sources are rejected. Clone/download those manually first if you intentionally want to trust them.
- Git is run with terminal prompts disabled, and shallow clones use no tags.
- Git is configured to reject `file://` and `ext::` transport expansion during clone.
- ZIP path traversal is rejected.
- ZIP symlinks are rejected.
- Symlinks inside copied skill bundles are rejected or skipped.
- Skill names and frontmatter are validated before installation.
- Bundle/archive size and file-count limits are enforced.
- Existing skills are not replaced unless an update or explicit `--force` is used.
- Remote Git repositories are shallow-cloned and are not executed during installation.

Skills remain instructions and resources, not privileged code. They do not bypass Loom's `PermissionEngine`, tool exposure rules, browser policy, sandbox policy, MCP policy, or approval flow.

When a workflow stages and later runs a bundled script, that process execution still crosses the same Loom permission and filesystem-containment boundaries as any other process call.

## Current compatibility

Loom v2 is designed around the common Agent Skills / Codex-style `SKILL.md` bundle model and supports instruction-only skills as well as bundles containing references, templates, scripts, and assets.

The installer currently supports local directories, local ZIP files, HTTPS remote ZIP files, HTTPS Git repositories, GitHub repository URLs, and GitHub `/tree/<branch>/<path>` URLs. Git must be available on `PATH` for repository sources.

Private repository authentication is non-interactive. It only works when HTTPS Git credentials are already configured and usable without prompts. A hosted remote Skill registry/search marketplace is not part of this version.
