# Loom

Loom is a personal, general-purpose AI agent built for real tool use. The current Runtime v2 foundation is modeled around durable sessions, frozen per-step execution context, explicit permission modes, model/tool loops, workspace tools, command execution, cancellation, recovery, and observable events.

The initial core was cleanly extracted from the validated generic Agent architecture in `yuchenm1303-png/ecommerce-agent` at `feat/commerce-ai-platform@04cceaf2efd8aea867989781fb3c91ebb13cb3c9`. No Listing, Makro, supplier, product-field, or other commerce production code is included here.

## What exists today

- Provider-neutral model capabilities and stable Agent roles
- OpenAI and OpenAI-compatible runtime
- `agent.fast`, `agent.reasoning`, and `agent.vision` role contracts
- Durable Agent sessions and event stream
- Repeated Model → Tool → Observation → Model turn loop
- Frozen `StepContext` for each model sampling step
- Per-step `ToolRouter` separated from the installed `ToolRegistry`
- Tool exposure contract ready for direct/deferred/code-mode/hidden routing
- Central `ToolOrchestrator` for argument validation and permission decisions
- Permission profile and approval policy separated at the runtime boundary
- Persistent per-session permission modes
- Native project-workspace binding at session creation
- Tool error observations and approval resume / deny flow
- Cancellation token and interrupted-session recovery
- Token usage accounting
- Workspace-scoped file listing, reading, and recursive text search
- Full-file writes and exact-block replacement
- Process execution with argv, cwd, timeout, captured stdout/stderr, and `shell=False`
- Secret-like environment variables stripped from child processes
- Saved sessions remember both project workspace and permission mode
- Backward-compatible loading of Loom v1 session snapshots

Private model chain-of-thought is neither requested nor persisted. Loom stores only observable messages, tool activity, lifecycle events, usage, and session/workspace state.

## Install

Python 3.11+ is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Run in a project directory

Loom treats the directory where you launch it as the workspace for a new session:

```powershell
cd C:\path\to\your-project
$env:DASHSCOPE_API_KEY="your-key"
loom
```

You can also select an explicit existing directory:

```powershell
loom --workspace C:\path\to\your-project
```

A resumed session keeps the workspace and permission mode saved with that session:

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

Or set the default for new sessions:

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

A permission change is persisted with the session. Loom refuses to change the mode while a turn is running or paused for approval, so a model step cannot be generated under one permission snapshot and executed under another.

`full-access` means registered Loom tools do not repeatedly stop for approval. It does **not** disable basic execution mechanics: workspace file tools still reject paths outside their configured workspace, command execution still uses `shell=False`, secret-like environment variables are still stripped, and command timeout/output bounds remain active.

Important current limitation: Loom does not yet have Codex-style OS sandboxing. A process launched by `run_workspace_command` is a real host process and can access resources that the operating system account can access through its arguments. That is why `workspace` mode still asks before process execution. Use `full-access` only when you intentionally trust the active agent and project.

## StepContext and Tool routing

Runtime v2 creates a frozen `StepContext` for each model sampling step. The snapshot currently contains:

- session / turn / step identity
- selected workspace
- model profile identity
- permission mode and resolved permission profile
- approval policy
- the exact `ToolRouter` exposed to that sampling step

If the model returns tool calls, Loom executes those calls against the same step identity and routing snapshot. When an approval boundary pauses execution, the step ID is persisted so resume stays bound to the same model step.

`ToolRegistry` now means “tools installed in Loom,” while `ToolRouter` means “tools exposed to this model step.” The first implementation directly exposes tools marked `DIRECT`; the exposure contract already reserves `DEFERRED`, `CODE_MODE_ONLY`, and `HIDDEN` for future Tool Search / Code Mode work.

## DashScope defaults

With `DASHSCOPE_API_KEY` or the legacy-compatible `AI_API_KEY`, Loom defaults to:

- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Model: `qwen-plus`

Override the model when needed:

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

## Coding-agent tools

Current direct tools:

- `calculator`
- `echo`
- `list_workspace_files`
- `read_workspace_text`
- `search_workspace_text`
- `write_workspace_text`
- `replace_workspace_text`
- `run_workspace_command`
- legacy compatibility alias `write_workspace_note`

`run_workspace_command` executes one program directly with an argv array and `shell=False`; shell expansion is not implicitly enabled. Its working directory must resolve inside the current workspace. It supports a bounded timeout and captured stdout/stderr.

Before launching a child process, Loom removes environment variables whose names look like API keys, tokens, passwords, secrets, or private keys.

Workspace file tools mechanically reject paths that resolve outside the selected workspace. Exact-block replacement also fails closed if the old text is absent or matches more than one location.

## CLI

Interactive mode:

```powershell
loom
```

One-shot task:

```powershell
loom "Find the failing test, fix it, and run the relevant test command."
```

Useful interactive commands:

- `/new [path]` — create a new session; reuse the current workspace and permission mode unless a path is given
- `/sessions` — list saved sessions, workspaces, and permission modes
- `/use <session-id>` — switch sessions
- `/session` — show current session id
- `/workspace` — show the current workspace
- `/permissions [mode]` — show or change the current permission mode
- `/usage` — show token usage
- `/help` — show commands
- `/quit` — exit

Runtime state defaults to `~/.loom/agent_runtime/sessions/`. Set `LOOM_HOME` to change the state root. Project files remain in the selected project workspace; Loom runtime state is not stored inside the project unless you explicitly point `LOOM_HOME` there.

## Test

```powershell
python -m pytest
```

The suite covers, among other things:

- generic Agent model configuration
- complete calculator Tool Loop
- per-step identity in model events
- native workspace binding
- read-only recursive workspace search
- backward-compatible approval mode
- workspace mode automatic file mutation
- workspace mode approval for process execution
- full-access command execution without repeated approval
- read-only mutation denial
- permission-mode persistence
- Loom v1 session snapshot compatibility
- fail-closed exact replacement
- command output capture
- secret environment stripping for subprocesses

GitHub Actions runs installation, `compileall`, pytest, and a CLI smoke test on pushes to `main` and on pull requests.

## Runtime v2 direction

The current work is the **foundation**, not the finished Codex-equivalent runtime. The next layers are intentionally ordered around execution correctness:

1. Unified Exec Runtime + `ProcessStore` + interactive process handles + process-tree cancellation
2. first-class `apply_patch` + atomic verification + `DiffTracker`
3. richer `WorldState` and context-diff injection
4. canonical history repair / compaction
5. durable Goal and Queue
6. Multi-Agent + AgentGraph
7. Memory
8. Web Search
9. Browser / Computer Use
10. MCP / Skills / Tool Search / Code Mode

The design rule is: Thread state is durable; Turn/Task/Process execution is temporary; tools all pass through one routing and permission boundary.
