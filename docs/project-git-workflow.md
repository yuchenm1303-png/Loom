# Project Git workflow

Projects remain rooted in local directories. The repository association is
derived from Git's `origin`, so it stays consistent with terminal operations.
GitHub HTTPS and SSH remotes are recognised; no credentials are stored in the
project registry. Explicit association sets `origin` to a GitHub HTTPS URL.

The project panel offers Git initialisation, repository association, local
branch creation/switching, worktree creation, fetch, fast-forward pull, normal
push with upstream registration, and draft or ready pull requests. Existing
staging, diff and commit controls remain in the same panel. The conversation
bar shows the project, branch, upstream counts, worktree state and tracked
line changes and opens this panel.

Worktrees start from HEAD in a sibling `<project>-worktrees` directory and are
registered as separate projects. Clean managed worktrees can be removed;
modified or active worktrees are protected. Only merged local branches can be
deleted. The resulting project can immediately start
a conversation. They do not copy uncommitted files.

RPCs: `project/git_repository`, `git_init`, `git_bind`, `git_switch_branch`,
`git_create_worktree`, `git_fetch`, `git_pull`, `git_push`, `git_create_pr`, all
under the `project/` namespace. Each requires `projectId`; mutations respect
the active-project guard. Workflow operations sharing a Git common directory
are serialized by a nonblocking lock.

PR creation uses the configured GitHub connector. It checks that the remote
head equals local HEAD, reuses an existing open PR for the same head/base, and
returns its URL. Git transport uses the host's existing Git credential setup.
No force push, reset, merge conflict resolution or automatic deployment is
performed by these controls.
