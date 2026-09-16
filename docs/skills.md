# Skills Runtime v2

Loom supports reusable Agent Skills bundles built around Codex-compatible `SKILL.md` files. Skills can be discovered from a repository, installed into the Loom user home, searched and loaded on demand, and can include scripts, references, templates, and assets.

## Install and manage skills

The `loom` executable has a dedicated `skill` command family. These commands do not require an AI model or API key.

```bash
loom skill install ./my-skill
loom skill install ./my-skill.zip
loom skill install https://github.com/owner/repository
loom skill install https://github.com/owner/repository/tree/main/path/to/skill
loom skill install https://github.com/owner/repository/blob/main/path/to/skill/SKILL.md

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

`--force` can replace an existing Loom-managed skill. It intentionally cannot overwrite a manual/unmanaged skill directory. Likewise, `update`, `update --all`, and `remove` only mutate skills carrying a valid Loom installation manifest.

Installed user skills live under:

- `<LOOM_HOME>/skills`

The legacy-compatible user root remains supported for manually managed skills:

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
- `skill_read_resource`: read a bounded UTF-8 text file bundled with a skill.
- `skill_stage_bundle`: copy a validated inert skill bundle into `.loom/skill-runs/<name>` in the current workspace.

A typical model flow is:

1. Search for a relevant skill.
2. Load the selected skill by exact name.
3. Read references/templates directly when needed.
4. If the workflow needs bundled scripts or assets, stage the bundle into the workspace.
5. Run scripts with Loom's normal process tools.

Loading and staging never execute bundled code.

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

Text resources can be read without copying the bundle into the project. Scripts and other assets can be staged into the current workspace so existing Sandbox and Process Runtime rules continue to apply.

Native executable/library/disk-image payloads such as `.exe`, `.dll`, `.so`, `.dylib`, `.msi`, and `.dmg` are rejected by the managed installer/stager. Script source files such as Python, JavaScript, PowerShell, or shell remain valid skill resources, but copied files have executable permission bits removed where the platform supports Unix modes.

## Installation safety

Installing or updating a skill is deliberately inert. Loom does not execute package-manager hooks, shell commands, Python files, JavaScript files, dependency installers, or `git` subprocesses during installation.

Remote installation is deliberately narrower than local installation. The safe default currently accepts only public HTTPS GitHub URLs:

- `https://github.com/<owner>/<repo>`
- `https://github.com/<owner>/<repo>/tree/<ref>/<path>`
- `https://github.com/<owner>/<repo>/blob/<ref>/<path>/SKILL.md`

Loom downloads the GitHub source archive as data and validates it before publication. `http://`, `git://`, `ssh://`, arbitrary remote ZIP hosts, embedded URL credentials, and non-standard GitHub ports are rejected. Local directories and local ZIP files remain supported.

The installer applies additional supply-chain boundaries:

- Compressed archive size, expanded bundle size, per-file size, and file-count limits are enforced.
- ZIP path traversal, non-portable path components, duplicate normalized paths, and archive symlinks are rejected.
- Symlink files/directories inside local skill bundles are rejected.
- Common VCS/cache/dependency trees such as `.git`, `.hg`, `.svn`, `__pycache__`, and `node_modules` are not copied as skill payload.
- Native executable payloads and application bundles are rejected.
- Existing manual/unmanaged skills are never overwritten or removed by Loom management commands.
- Install/update publication uses a staged replacement with rollback so an interrupted replacement does not intentionally delete the previous managed copy first.
- Installed content receives a SHA-256 tree digest and an `execution_policy: inert-on-install` provenance record in `.loom-skill.json`.
- Copied skill files are stripped of executable permission bits where supported.

This installer remains a user-facing management command, not a model-exposed install tool. A model cannot silently pull and install a new remote skill during an Agent turn.

## Resource and staging safety

`skill_read_resource` rejects parent traversal, absolute/non-portable paths, Loom internal metadata, VCS/dependency paths, and symlink traversal. It reads bounded UTF-8 text only, rejects binary-looking content, and applies Loom's secret redaction before returning text to the model.

`skill_stage_bundle` is a mutating tool, so the existing permission engine remains authoritative. Staging also rejects symlinked workspace `.loom`/staging paths, refuses to replace unrecognized staging directories, rejects symlink/native executable bundle content, enforces size limits, strips executable bits, writes an inert staging marker, and swaps staged content with rollback semantics.

## Execution boundary

Skills are instructions and inert resources, not privileged code. They do not bypass Loom's `PermissionEngine`, tool exposure rules, browser policy, sandbox policy, MCP policy, process policy, or approval flow.

When a workflow later asks Loom to run a bundled script, that execution is a separate action through Loom's normal sensitive process/`exec` tools. It therefore crosses the same permission, environment, filesystem-containment, and sandbox boundaries as any other command.

Do not store real credentials in `SKILL.md` or supporting files. Secret redaction is defense in depth, not a credential vault.

## Current compatibility

Loom v2 is designed around the common Agent Skills / Codex-style `SKILL.md` bundle model and supports instruction-only skills as well as bundles containing references, templates, scripts, and assets.

The managed installer currently supports local directories, local ZIP files, public GitHub repository URLs, GitHub `/tree/<ref>/<path>` URLs, and GitHub `SKILL.md` blob URLs. Private repository authentication and arbitrary third-party remote registries are intentionally not enabled in the safe-default path yet; they should be added later through an explicit trusted-source/authentication layer rather than by allowing unrestricted network fetches.
