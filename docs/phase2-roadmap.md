# Loom Phase 2 — Agent Platform Roadmap

Phase 1 established the capability plane: durable Runtime v2, Browser, MCP, Tool Search, Skills, and Code Mode. Phase 2 changes the center of gravity from adding tools to making Loom a durable, product-grade local Agent platform.

## Goal

Move Loom from a capable single-process agent into a system that can power multiple first-party clients, stream real execution state, isolate coding work safely, support concurrent agents without workspace collisions, and survive long-running local use.

The architectural target is:

```text
Desktop / CLI / Web / future editor clients
                |
          Loom App Server
      JSON-RPC + event stream
                |
      Thread / Turn / Item model
                |
          CoreAgentRuntime
  model + tools + permissions + state
                |
 Browser / MCP / Skills / Code Mode
 Exec / Sandbox / Worktrees / Memory
                |
 Durable stores + workspace filesystem
```

The App Server must be an adapter over the existing runtime, not a second agent loop.

## Design principles

1. One runtime, many clients. Desktop, CLI, Web, and future editor integrations must not implement their own model/tool loops.
2. Protocol state is observable state. Private chain-of-thought is never exposed as a streaming primitive.
3. Durable intent, ephemeral execution. Threads, goals, queues, project/worktree metadata, memory, and canonical history may persist; Python stacks, Futures, PTYs, and subprocess handles do not.
4. Every side effect crosses the same ToolOrchestrator / PermissionEngine / sandbox path.
5. A permission grant is scoped. Approval of a container operation never implicitly grants its nested operations.
6. Multi-agent concurrency must use isolated workspaces before it becomes proactive.
7. Windows is a first-class target because Loom is primarily used locally on Windows.
8. Backward compatibility is migration, not architecture. Existing CLI/Web tools may remain as wrappers while new primitives become canonical.

## Milestone 2.1 — App Server + stable local protocol

Add `loom-app-server` as the single local control plane for rich clients.

Initial transport:

- stdio JSON-RPC 2.0 / JSONL;
- versioned protocol handshake;
- bounded ingress/outbound queues and explicit overload errors;
- later local socket transport without changing the protocol model.

Core primitives:

- `Thread` — durable conversation/session identity;
- `Turn` — one user-initiated unit of agent execution;
- `Item` — observable turn content such as user text, assistant text, tool call, terminal command, file edit, approval request, browser action, and error.

Initial methods:

- `initialize`
- `thread/start`
- `thread/resume`
- `thread/list`
- `thread/read`
- `thread/fork`
- `turn/start`
- `turn/interrupt`
- `approval/respond`
- `runtime/status`

Initial notifications:

- `thread/started`
- `turn/started`
- `item/started`
- `item/delta`
- `item/completed`
- `approval/requested`
- `turn/completed`

The existing `FileAgentSessionStore`, event stream, PermissionEngine, Browser/MCP/Skills/Code Mode layers remain authoritative.

Exit criteria:

- a test client can create/resume a thread, start/interrupt a turn, approve a tool, reconnect, and reconstruct authoritative state without importing `AgentRuntime` directly;
- CLI and Web can begin migrating to the protocol without behavioral divergence.

## Milestone 2.2 — True provider streaming + normalized runtime events

Replace UI polling as the primary progress mechanism with a runtime event bus and provider-normalized streaming.

Add normalized events for:

- assistant text delta;
- tool-call argument delta when the provider supports it;
- model step start/end;
- tool/process progress;
- browser/MCP/Code Mode progress;
- usage and completion metadata.

Requirements:

- no private reasoning token stream;
- final canonical message is still committed atomically;
- a dropped UI connection cannot corrupt the active Turn;
- reconnect reads durable state plus any still-live process state.

This milestone turns the current Web UI live feedback into genuine streaming where the provider supports it.

## Milestone 2.3 — Native Windows Desktop shell

Build a real local desktop client, initially with PySide6, that launches/connects to `loom-app-server` instead of embedding a second runtime.

First desktop surface:

- project/thread sidebar;
- streaming chat transcript;
- approval cards;
- runtime activity panel;
- terminal/process panel;
- current turn diff viewer;
- Browser activity status;
- Agent tree view;
- permission/sandbox status;
- workspace chooser.

Package as a Windows executable after the protocol and shell are stable.

The existing Web UI remains a debug/fallback client.

Exit criteria:

- user can launch Loom locally without opening a browser;
- app restart can reconnect to durable threads;
- API credentials remain in the Python/runtime process and are never delivered to UI widgets.

## Milestone 2.4 — Unified Exec v2 + PTY

Consolidate the current command/process surface behind a canonical execution primitive while retaining compatibility wrappers.

Target primitives:

- `exec` — start one-shot or interactive command execution;
- `exec/wait` — wait/poll for completion;
- `exec/write` — stdin input;
- `exec/interrupt`;
- `exec/terminate`;
- terminal resize for PTY sessions.

Requirements:

- argv-based execution, no implicit shell interpolation;
- real PTY when requested;
- bounded output and durable final transcript;
- structured process lifecycle events;
- cancellation and process-tree cleanup;
- PermissionEngine and sandbox evaluation before process creation.

The old `run_workspace_command`, `start_workspace_command`, poll/write/interrupt/terminate tools remain temporary aliases until migration is complete.

## Milestone 2.5 — Projects, Thread Forks, and managed Git worktrees

Add a project layer above a raw workspace path.

A `Project` contains:

- stable project id;
- one or more workspace roots;
- repository metadata when Git is present;
- associated threads;
- managed worktree records.

Add:

- `thread/fork` with canonical history copy through a safe Turn boundary;
- managed worktree create/list/remove;
- new thread in isolated worktree;
- optional sub-agent worktree assignment;
- worktree-aware diff and terminal cwd.

This milestone is a prerequisite for proactive multi-agent coding because independent agents should not race on the same checkout by default.

## Milestone 2.6 — Security plane v2

Extend permission checks into real OS and network isolation.

Priority order:

1. Windows sandbox backend with honest capability probing and fail-closed `required` mode;
2. domain-scoped managed network proxy for sandboxed processes;
3. OS keyring-backed Secret/Vault references;
4. execution-time credential injection without persisting secret values in Session, WorldState, memory, tool results, or logs;
5. richer named permission profiles and scoped approval records.

The user-prompt secret persistence gap must be addressed here by redacting/sealing detected secret values before durable transcript persistence, without silently changing ordinary user content.

## Milestone 2.7 — Multi-Agent v2

Evolve AgentGraph from durable independent children into conflict-safe coordinated coding agents.

Add:

- task-path / role metadata;
- worktree-per-agent isolation by default for coding delegation;
- shared tree token/step budget;
- parent steering and durable asynchronous messages;
- structured child result summaries;
- merge/review handoff rather than direct concurrent edits to the same checkout;
- deterministic depth/concurrency/budget enforcement.

Proactive agent spawning remains off by default until isolation, budgets, and merge boundaries are proven.

## Milestone 2.8 — Context and provider efficiency

After the protocol/execution foundation stabilizes, optimize model cost and latency:

- provider-specific cached-context / state-delta transport;
- WorldState delta delivery using the existing stable digest;
- richer compaction checkpoint metadata;
- per-thread and per-agent rollout budgets;
- model switching by step/role;
- image/context budget normalization.

This milestone must preserve the provider-neutral Runtime contract rather than leaking one provider's response state into core persistence.

## Explicit Phase 2 non-goals

These are valuable but should not distract from the execution/product foundation:

- public plugin marketplace;
- remote cloud control plane;
- autonomous unattended infinite execution;
- generic workflow/automation builder;
- mobile clients;
- arbitrary extension code running outside Loom permissions;
- pretending permission prompts are equivalent to OS sandboxing.

## Recommended implementation order

```text
2.1 App Server / Protocol
        ↓
2.2 Real Streaming
        ↓
2.3 Native Desktop
        ↓
2.4 Unified Exec + PTY
        ↓
2.5 Project / Worktree / Fork
        ↓
2.6 Windows Sandbox + Network + Vault
        ↓
2.7 Multi-Agent v2
        ↓
2.8 Context / Provider efficiency
```

2.4 and 2.6 may partially run in parallel after the protocol is stable, but 2.7 should not become proactive before 2.5 and the security boundaries are complete.

## Phase 2 exit definition

Phase 2 is complete when a Windows user can:

1. launch `Loom.exe` into a native desktop window;
2. open a project and create/resume/fork durable threads;
3. see real assistant/tool/terminal streaming without exposing private reasoning;
4. run interactive terminal commands through a unified PTY execution path;
5. review diffs and approvals from the same client;
6. execute in a real reported Windows sandbox when policy requires it;
7. use secrets without durable plaintext persistence;
8. delegate coding work to isolated child worktrees and merge/review the result;
9. restart the desktop/app-server without losing canonical thread, goal, queue, project, AgentGraph, or memory state.

At that point Loom is no longer just a collection of Agent capabilities; it has a stable local Agent platform boundary that can support future desktop, editor, automation, and plugin surfaces without rewriting the core runtime.
