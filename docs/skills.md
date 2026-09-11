# Skills Runtime v2

Loom supports reusable Codex-compatible / Agent Skills-style `SKILL.md` workflows through the default `SkillRuntime` layer, plus a safe user-controlled installer for complete skill bundles.

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

## Installing skills

The `loom` entrypoint now has a dedicated skill-management command that does not require a model provider or API key:

```text
loom skill install ./my-skill
loom skill install ./my-skill.zip
loom skill install https://github.com/OWNER/REPO
loom skill install https://github.com/OWNER/REPO/tree/main/path/to/skill
loom skill list
loom skill update release-check
loom skill remove release-check
```

`loom-skill` is also installed as a direct convenience entrypoint.

Managed skills are installed under `<LOOM_HOME>/skills` by default. `LOOM_HOME` still applies; `loom skill --home <path> ...` and `loom skill --root <path> ...` can override the location.

For safety, remote v1 installation accepts public `https://github.com/...` sources only. A repository URL is accepted when the repository contains exactly one discoverable `SKILL.md`; repositories containing multiple skills must use a specific GitHub `/tree/<ref>/<skill-path>` URL.

## Inert installation policy

Installing a skill never executes package content. Loom only downloads/copies, validates, hashes and publishes the bundle.

The installer applies the following boundaries before a managed skill becomes visible:

- HTTPS-only remote installation from the approved GitHub host set.
- Bounded compressed download size, expanded size, individual file size and file count.
- ZIP path-traversal rejection.
- Symlink rejection for both local and archived bundles.
- Native executable payload rejection for common executable/library/disk-image formats.
- VCS/cache directories such as `.git`, `.hg`, `.svn`, `__pycache__` and `node_modules` are not copied.
- Copied regular files are stripped of executable permission bits where the platform supports Unix modes.
- Existing manual/unmanaged skills are never overwritten or deleted by `--force`, `update`, or `remove`.
- Updates are staged and swapped into place atomically; the previous managed copy is restored if publication fails.
- A `.loom-skill.json` management record stores the source, content hash and inert-install policy. This metadata is not exposed to the model through `skill_resource`.

This installer is intentionally a user-facing CLI capability, not an Agent tool. A model cannot silently install a new remote skill during a turn.

## On-demand model exposure

Skill bodies are not placed into the initial model context. Loom exposes three read-only runtime tools:

- `skill_search`: searches skill metadata for the current workspace.
- `skill_load`: loads one exact skill body after discovery.
- `skill_resource`: lists or reads supporting UTF-8 text files inside an installed skill bundle.

A typical model flow is:

1. Search for a relevant skill.
2. Load the selected skill by exact name.
3. Inspect referenced supporting files with `skill_resource` when needed.
4. Follow the workflow using normal Loom tools.

The body only becomes visible to the model after `skill_load` succeeds. Supporting resources remain inert and are only read on demand.

`skill_resource` enforces bundle-relative paths, rejects `..`, rejects symlinks, hides Loom management metadata, limits directory listings, bounds text-resource size, rejects binary/non-UTF-8 reads and redacts secret-shaped values before returning text to the model.

## Scripts and command execution

A skill bundle may contain Python, JavaScript, shell scripts, templates and references, but installing or loading the bundle does not execute them.

When a loaded skill asks Loom to run a bundled script, execution must still go through Loom's normal sensitive `exec` / process runtime. Therefore the existing permission mode, approval flow, workspace restrictions, environment policy and sandbox boundary remain authoritative. A skill never receives a privileged script runtime of its own.

## Security boundary

Skills are instructions and inert bundle resources, not a permission grant. They do not bypass Loom's `PermissionEngine`, tool exposure rules, browser policy, sandbox policy, MCP policy, process policy, or approval flow.

Loom also applies these discovery boundaries:

- Hidden directories below a skill root are skipped.
- Resolved `SKILL.md` paths must remain inside the discovery root, preventing symlink escape.
- Skill files are size bounded.
- Skill instructions are bounded and secret-shaped values are redacted before being returned to the model.
- Duplicate names use deterministic first-root-wins precedence.

Do not store real credentials in `SKILL.md` or supporting files. Redaction is defense in depth, not a secret vault.

## Compatibility scope

The installer and runtime are designed around the common Agent Skills bundle convention: a directory with `SKILL.md` plus optional `scripts/`, `references/`, templates and other supporting files. Instruction-only Codex-compatible skills continue to work unchanged.

The v2 installer deliberately does not run dependency-install hooks, package lifecycle scripts, native executables, or remote arbitrary shell commands. Skills that require dependencies can instruct Loom to install them later through normal sensitive tools, where the user retains the existing Loom approval and sandbox protections.
