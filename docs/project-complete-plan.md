# Loom Project System Completion Plan

This is the implementation path for turning Loom projects from a basic sidebar grouping into a Codex-like project surface.

## Current baseline

Loom already supports durable projects by workspace root:

- list projects
- create projects from folders
- rename projects
- remove projects without deleting files
- start a thread inside a project
- move an idle thread between projects
- show project thread counts

This is not yet a full Codex-style project model because a project does not yet own all execution context, settings, review state, and repository affordances.

## Phase 1 — Project safety and durable instructions

Status: started.

Goals:

- Block running-thread project moves at the service boundary.
- Return a product-facing error for running-thread project moves.
- Persist project-level instructions in `projects.json`.
- Expose an RPC endpoint for saving project instructions.
- Advertise project-instructions support during initialize.

Implemented endpoint:

```text
project/set_instructions
```

Input:

```json
{
  "projectId": "pxxxxxxxxxxxx",
  "instructions": "Project-level working instructions..."
}
```

Output:

```json
{
  "project": {
    "id": "pxxxxxxxxxxxx",
    "name": "Loom",
    "root": "C:/Users/me/Loom",
    "instructions": "Project-level working instructions...",
    "threadCount": 3,
    "createdAt": "...",
    "updatedAt": "..."
  }
}
```

## Phase 2 — Project details UI

Next frontend surface:

- Open project details from the sidebar project row actions.
- Show project name, root path, thread count, and latest activity.
- Edit and save project instructions.
- Show a disabled/safe state while any project thread is running.
- Make movement errors Chinese and action-oriented instead of raw RPC errors.

## Phase 3 — Project instructions in runtime context

Project instructions should become part of the agent context without polluting the visible user message.

Preferred design:

- Store instructions on the project record.
- At turn start, resolve the thread's project.
- Inject project instructions into the session/system context layer.
- Do not duplicate instructions into transcript text.
- If instructions change while a thread is running, apply them to the next turn only.

## Phase 4 — Workspace/Git project panel

Add Codex-like project affordances:

- project file tree
- current Git branch
- changed files
- inline review dock entry
- commit/PR action surface
- project-scoped shell status

## Phase 5 — Project-scoped defaults

Add per-project defaults:

- model profile
- permission mode
- enabled capabilities
- terminal shell
- browser engine/session setting
- memory behavior
- attachment policy

## Phase 6 — Full project lifecycle polish

Complete edge cases:

- drag/drop move guard
- archived thread project behavior
- project deletion confirmation with exact consequences
- stale/missing folder handling
- duplicate root detection UX
- multi-root/worktree design, only after the single-root model is stable
