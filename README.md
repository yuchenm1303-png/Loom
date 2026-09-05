# Loom

Loom is a personal, general-purpose AI agent built for real tool use. Runtime v2 is organized around durable thread state, frozen per-step execution context, explicit permission modes, managed processes, atomic file edits, crash recovery, durable goals/queues, authoritative context checkpoints, independent sub-agents, durable long-term memory, and an honest OS-sandbox boundary.

The initial core was cleanly extracted from the validated generic Agent architecture in `yuchenm1303-png/ecommerce-agent` at `feat/commerce-ai-platform@04cceaf2efd8aea867989781fb3c91ebb13cb3c9`. No Listing, Makro, supplier, product-field, or other commerce production code is included here.

## What exists today

- Provider-neutral model capabilities and stable Agent roles
- OpenAI and OpenAI-compatible runtime
- `agent.fast`, `agent.reasoning`, and `agent.vision` role contracts
- Durable Agent sessions and observable event stream
- Repeated Model → Tool → Observation → Model turn loop
- Frozen `StepContext` for each model sampling step
- `ToolRegistry` separated from the per-step `ToolRouter`
- Tool exposure contract ready for direct/deferred/code-mode/hidden routing
- Central `ToolOrchestrator` for argument validation and permission decisions
- Permission profile and approval policy separated at the runtime boundary
- Persistent per-session permission modes
- Native project-workspace binding at session creation
- Managed foreground/background command processes with process IDs
- Poll / stdin / interrupt / terminate lifecycle operations
- Timeout and cancellation-aware process-tree termination
- Bounded live command output plus final transcripts
- First-class structured `apply_patch` runtime
- Atomic multi-file add/update/delete/move operations
- Git-independent per-turn `DiffTracker`
- Crash-safe tool-history repair
- SQLite/WAL durable goals and future-turn queueing
- Transactional queue dispatch reconciliation
- Goal continuation with token budgets
- Explicit OS-sandbox planning and capability reporting
- Linux Bubblewrap backend when available and usable
- Model-visible `get_sandbox_status`
- Authoritative per-step `LOOM_RUNTIME_STATE` / WorldState envelope
- Stable WorldState digest ready for future state-delta transport
- Atomic archived context checkpoints
- Model-driven context compaction with usage accounting
- SQLite/WAL durable `AgentGraph`
- Independent sub-agent sessions with inherited workspace/permissions
- Model-facing spawn/message/wait/list/close sub-agent tools
- Durable cross-agent follow-up via the normal Loom queue
- SQLite/WAL long-term memory with separate extraction and consolidation
- Global and workspace-scoped memory isolation
- Secret redaction before memory extraction and persistence
- Transient relevance-based memory injection without polluting canonical history
- User-controlled memory inspection, search, extraction, consolidation, and forgetting
- Secret-like environment variables stripped from child processes

Private model chain-of-thought is neither requested nor persisted. Loom stores only observable messages, tool activity, lifecycle events, usage, durable thread/workspace state, archived context checkpoints, durable agent-graph metadata, and validated long-term memory records.

## Install

Python 3.11+ is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Run in a project directory

```powershell
cd C:\path\to\your-project
$env:DASHSCOPE_API_KEY="your-key"
loom
```

Or select an explicit existing workspace:

```powershell
loom --workspace C:\path\to\your-project
```

Resume a saved session:

```powershell
loom --session <session-id>
```

`--workspace` and `--session` are intentionally mutually exclusive so a resumed session cannot silently jump to another project.

## Permission modes

A new session defaults to `approval` for backward compatibility.

| Mode | Read-only tools | Workspace file mutation | Process execution |
| --- | --- | --- | --- |
| `read-only` | automatic | denied | denied |
| `approval` | automatic | asks | asks |
| `workspace` | automatic | automatic | asks |
| `full-access` | automatic | automatic | automatic |

Select a mode when creating a session:

```powershell
loom --permission-mode workspace
loom --permission-mode full-access
```

Or set the default:

```powershell
$env:LOOM_PERMISSION_MODE="workspace"
loom
```

Inside interactive Loom:

```text
/permissions
/permissions read-only
/permissions approval
/permissions workspace
/permissions full-access
```

A permission change is persisted with the session. Loom refuses to change it while a turn is active. Existing managed processes for that session are terminated on a permission transition because an already-running process cannot be retroactively constrained by a new profile.

`full-access` means registered Loom tools do not repeatedly stop for approval. File tools still enforce their mechanical path rules, command execution remains `shell=False`, secret-like environment variables remain stripped, and timeout/output bounds remain active.

Sub-agents never gain authority merely by being spawned. A child inherits the parent's workspace and permission mode, and every child tool call still crosses the normal Loom permission/sandbox pipeline.

## OS sandbox policy

Permission/approval and OS isolation are separate concepts.

Loom supports three runtime sandbox policies:

- `auto` — use a supported OS sandbox when it is actually available; otherwise continue with the legacy Loom execution boundary and report that OS isolation was not enforced.
- `required` — fail closed if the requested non-full-access command cannot be placed in a real supported OS sandbox.
- `off` — explicitly disable OS sandboxing.

The default is `auto`. Configure it with:

```powershell
$env:LOOM_SANDBOX_POLICY="auto"
$env:LOOM_SANDBOX_POLICY="required"
$env:LOOM_SANDBOX_POLICY="off"
```

Current real backend support:

- Linux: Bubblewrap (`bwrap`), only after a runtime probe succeeds
- macOS: not implemented yet
- Windows: not implemented yet

For a workspace sandbox on Linux, Loom mounts the host root read-only, makes the selected workspace writable, gives the process an isolated `/tmp`, and re-mounts `.git`, `.loom`, and `.agents` read-only when those paths exist. Network isolation is **not** implemented yet and is reported as such.

`full-access` intentionally disables the OS sandbox even if Bubblewrap is available.

Every managed process snapshot records whether sandboxing was enforced, which backend was used, the effective sandbox mode, and the fallback reason when isolation was unavailable. The model can query the same information with `get_sandbox_status`.

## StepContext and WorldState

Runtime v2 creates a frozen `StepContext` for every model sampling step. The snapshot contains:

- session / turn / step identity
- selected workspace
- model profile identity
- permission mode and resolved permission profile
- approval policy
- exact `ToolRouter` exposed to that sampling step
- effective sandbox capability snapshot

The model also receives a transient authoritative `LOOM_RUNTIME_STATE` envelope containing the current workspace/model/permission/sandbox/tool state, durable goal/queue state, current turn diff, and active agent-tree topology. This envelope is not appended to canonical conversation history.

WorldState has a stable digest that excludes step identity. Adjacent model steps with unchanged runtime state therefore share the same digest, which leaves a clean path to future provider-specific delta injection without pretending stateless Chat Completions already saves those tokens.

If the model returns tool calls, Loom executes them against the same step identity and routing/permission snapshot. Approval resume stays bound to the same model step.

## Context checkpoints and compaction

Conversation compaction is loss-aware. Loom repairs tool-call/output pairing, chooses a safe boundary that starts at a real user message, archives the old canonical prefix atomically, and only then replaces the active prefix with a compacted summary.

`compact_context_with_model` runs a dedicated no-tools model request over the archived prefix, accounts for its model usage, and commits the resulting summary only after the checkpoint archive is durable. Active turns cannot be compacted.

The original archived messages remain available under the session's `context_checkpoints` directory instead of being silently discarded.

## Managed processes

The coding runtime exposes both one-shot and long-running process tools:

- `run_workspace_command`
- `start_workspace_command`
- `poll_workspace_process`
- `list_workspace_processes`
- `write_workspace_process`
- `interrupt_workspace_process`
- `terminate_workspace_process`

Commands execute from an argv array with `shell=False`. Working directories must resolve inside the selected workspace. Background processes keep stdin open for later interaction; foreground commands close stdin after the supplied input so programs waiting for EOF can terminate normally.

Managed process state is intentionally ephemeral. Durable intent belongs in Thread/Goal/Queue state, not in a serialized OS process handle. Interrupted recovery terminates any still-managed processes before repairing canonical history.

## File editing and turn diffs

Primary structured editing uses `apply_patch` with atomic preimage validation. A multi-file patch is fully planned and verified before commit; if any change is stale or invalid, no partial edit is accepted.

Supported change kinds include:

- add
- update
- delete
- move

`DiffTracker` records the net change for the current turn without depending on Git. Existing full-file write and exact-block replacement tools remain for compatibility and feed the same turn diff tracker.

## Durable goals and queue

Long-running intent is stored separately from the active Python execution stack.

A durable goal contains an objective, status, optional token budget, and usage accounting. Loom can continue an active goal in later turns without pretending to restore an old coroutine or instruction pointer.

The durable future-turn queue is SQLite/WAL backed. Queue items are claimed transactionally before dispatch and reconciled after interruption so a crash does not silently lose pending work.

Interactive commands include:

```text
/goal
/goal set <objective>
/goal budget <tokens> <objective>
/goal pause
/goal resume
/goal blocked
/goal complete
/goal continue [n]
/goal clear

/queue
/queue add <text>
/queue run [n]
/queue remove <id>
```

## Multi-Agent / AgentGraph

A Loom sub-agent is a real independent Loom session/thread, not a nested synchronous model call. Its conversation history, turns, goal, queue, failures, and usage are separate, while the parent/child/root relationship is persisted in SQLite/WAL `AgentGraph` state.

The model can use:

- `spawn_agent` — create an independent child and start delegated work in the background
- `send_agent_message` — append durable follow-up work to a child queue and optionally wake it
- `wait_agent` — wait briefly for an in-process child and read its durable status/result
- `list_agents` — inspect the current durable agent tree
- `close_agent` — cancel/terminate a child subtree and mark its graph nodes closed

History inheritance is selectable as `none`, `recent`, or `all`. Loom deliberately removes the currently executing `spawn_agent` control call before copying parent history so a child never inherits a half-written tool-call transcript.

Python `Future` objects are only temporary execution handles. AgentGraph relationships, child sessions, and child queued work remain durable across a Loom process restart; the restarted runtime reconstructs control from those stores rather than attempting to serialize a running Python stack.

Default safety limits are 16 active sub-agents per tree and depth 4. These are runtime constructor settings and can be lowered by embedders.

## Long-term memory

Long-term memory is intentionally separate from normal conversation history. A model cannot directly write or delete memory through an ordinary tool call.

The write path is:

```text
observable thread transcript
    → secret redaction
    → dedicated no-tools extraction request
    → validated pending candidates
    → separate consolidation boundary
    → canonical memory records
```

System/runtime context is excluded from extraction input. Candidate text is redacted again before persistence. Exact normalized duplicates collapse into one canonical memory and increment a source count.

Memory has two scopes:

- `global` — stable user preferences/facts useful across projects
- `workspace` — project architecture, decisions, constraints, and conventions tied to the current workspace

For each model request, Loom searches consolidated global + current-workspace memory using the recent user message as a query. Relevant matches are injected as a transient `loom_memory` system message. They are explicitly advisory and may be stale; they never override the current user message, WorldState, or observed tool results, and they are not persisted into canonical thread history.

The model has only read-only memory tools:

- `search_memory`
- `memory_status`

Explicit user/API controls are available in the CLI:

```text
/memory
/memory extract
/memory consolidate
/memory list [n]
/memory search <query>
/memory forget <memory-id>
```

`/memory forget` removes the canonical retrievable memory and candidate rows sharing its fingerprint so later consolidation cannot silently recreate it. Workspace-scoped memories can only be forgotten from a session attached to that workspace. Extraction summary rows remain audit metadata and are not used for retrieval.

## DashScope defaults

With `DASHSCOPE_API_KEY` or the legacy-compatible `AI_API_KEY`, Loom defaults to:

- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Model: `qwen-plus`

Override when needed:

```powershell
$env:LOOM_MODEL="qwen-plus"
loom
```

## Other providers

OpenAI-compatible endpoint:

```powershell
$env:LOOM_API_KEY="your-key"
$env:LOOM_BASE_URL="https://your-provider.example/v1"
$env:LOOM_MODEL="your-model"
loom --provider openai-compatible
```

Native OpenAI:

```powershell
$env:OPENAI_API_KEY="your-key"
$env:LOOM_MODEL="your-model"
loom --provider openai
```

Secrets are resolved at runtime and are not written into session snapshots or repository configuration.

## Current direct tools

- `calculator`
- `echo`
- `list_workspace_files`
- `read_workspace_text`
- `search_workspace_text`
- `write_workspace_text`
- `replace_workspace_text`
- `apply_patch`
- `get_turn_diff`
- managed process tools listed above
- `get_sandbox_status`
- durable goal state tools
- `spawn_agent`
- `send_agent_message`
- `wait_agent`
- `list_agents`
- `close_agent`
- `search_memory`
- `memory_status`
- legacy compatibility alias `write_workspace_note`

## Test

```powershell
python -m pytest
```

GitHub Actions runs installation, `compileall`, pytest, and a CLI smoke test on pushes to `main` and on pull requests.

The suite covers model/tool loops, per-step identity, permissions, persistence, managed process interaction, cancellation, timeout, secret stripping, atomic patches, turn diffs, durable goal/queue recovery, history repair, sandbox planning, sandbox fallback honesty, interrupted-process cleanup, WorldState/checkpoint compaction, parent→child model delegation, durable AgentGraph state, cross-agent queue recovery after restart, tree boundaries, agent safety limits, memory secret redaction, global/workspace isolation, transient retrieval injection, duplicate consolidation, forgetting boundaries, and memory restart persistence.

## Runtime v2 direction

Completed foundation layers now include:

1. managed Exec Runtime + `ProcessStore`
2. first-class atomic patching + `DiffTracker`
3. durable Goal / Queue + history recovery
4. explicit OS-sandbox planning with Linux Bubblewrap support
5. authoritative WorldState + archived context checkpoints + model compaction
6. durable Multi-Agent + AgentGraph + model-facing delegation tools
7. durable long-term Memory + extraction/consolidation/retrieval controls

Next high-value layers:

1. Web Search
2. Browser / Computer Use
3. MCP / Skills / Tool Search / Code Mode
4. richer provider-specific context-diff transport
5. additional native sandbox backends and network permissions

The core design rule remains: Thread/AgentGraph/Goal/Queue/context archives/validated memory are durable; Turn/Task/Process/Future execution is temporary; every tool crosses one routing and permission boundary; sub-agents never gain authority merely by being spawned; memory is advisory and user-controllable; OS isolation is reported truthfully rather than assumed.
