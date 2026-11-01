# Build mode: selecting and preparing a source project

The Repository card now offers both a native folder picker and the same project
records shown in the sidebar. Selecting an existing project moves the current
idle conversation through `thread/move_project`; it keeps its ID and transcript,
and does not register a duplicate project. A native folder selection still uses
the registry's root identity, so an already-registered folder resolves to its
existing project. The folder picker supports creating a directory in the native
dialog. No separate “build project” registry is created.

Source readiness is read from disk: not a repository, unborn repository, detached
HEAD, nested directory, unavailable/corrupt metadata, or ready. A proposal may be
discussed before readiness, but its Start action is disabled until the source is
ready. Selecting a nested directory explains which repository root to choose.

`projectRun/prepareWorkspace` is an explicit action with `confirmed: true` and an
exact `expectedPath`. The UI shows the intended operation before its button:

* Initialize: create local Git metadata (if absent) and an empty baseline commit,
  using per-command identity. It neither stages nor commits existing files, even
  if a preexisting unborn repository has staged files. Existing files remain in
  place; the existing review-and-commit control is visible before a proposal too.
  Only a separate explicit commit action includes those files in build inputs.
* Detached HEAD: create the displayed `codex/build-…` branch at the current commit,
  without checking out another revision or changing worktree contents.

The Host serializes preparation with the project scheduler and repository lock.
An internal agent session, active answer or unfinished persistent run prevents
source-folder changes at the service boundary, including moves from the sidebar.
Ready-state retries do not create another commit. No remote is added or pushed.

Tests cover registry reuse, folder locking between episodes, explicit path
confirmation, staged-file exclusion, initialization retries, nested repositories,
detached dirty worktrees, and a scripted end-to-end build from an empty directory
through independent review and local integration. Browser tests exercise selection,
cancel/error handling, existing-project selection, all three preparation states,
Start readiness, and the empty sidebar. These use scripted models and mocked
native dialogs, not a newly packaged desktop or paid model qualification.

Validation: 33 backend tests passed (workspace setup, project registry, conversation
moves, internal origins and program completion). After incorporating the concurrent
sandbox-permission change, 22 setup/move/origin/overview checks passed again. The
browser workflow checks, 21 projection tests, TypeScript check and production
renderer build passed. No provider request was sent.
