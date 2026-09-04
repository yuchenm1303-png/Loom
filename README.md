# Loom

Loom is a personal, general-purpose AI agent built for real tool use: durable sessions, model/tool loops, explicit approvals, workspace tools, command execution, cancellation, recovery, and observable execution events.

The initial core was cleanly extracted from the validated generic Agent architecture in `yuchenm1303-png/ecommerce-agent` at `feat/commerce-ai-platform@04cceaf2efd8aea867989781fb3c91ebb13cb3c9`. No Listing, Makro, supplier, product-field, or other commerce production code is included here.

## What exists today

- Provider-neutral model capabilities and stable Agent roles
- OpenAI and OpenAI-compatible runtime
- Explicit provider/model/credential configuration
- `agent.fast`, `agent.reasoning`, and `agent.vision` role contracts
- Durable Agent sessions and event stream
- Repeated Model → Tool → Observation → Model turn loop
- Tool registry and JSON-schema argument validation
- Tool error observations
- Explicit approval gate for mutating/sensitive tools
- Approval resume / deny flow
- Cancellation token and interrupted-session recovery
- Token usage accounting
- Workspace-scoped file listing and reading
- Recursive workspace text search
- Approval-gated full-file writes
- Approval-gated exact-block replacement
- Approval-gated process execution with argv, cwd and timeout
- Secret-like environment variables stripped from child processes
- Independent Codex-style terminal interface
- Saved sessions remember the project workspace they belong to

Private model chain-of-thought is neither requested nor persisted. Loom stores only observable messages, tool activity, lifecycle events, usage, and session/workspace state.

## Install

Python 3.11+ is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Run in a project directory

Loom now treats the directory where you launch it as the workspace for a new session, similar to a coding agent:

```powershell
cd C:\path\to\your-project
$env:DASHSCOPE_API_KEY="your-key"
loom
```

You can also select an explicit existing directory:

```powershell
loom --workspace C:\path\to\your-project
```

A resumed session keeps the workspace path saved with that session:

```powershell
loom --session <session-id>
```

`--workspace` and `--session` are intentionally mutually exclusive so a resumed session cannot silently jump to another project.

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

Read-only tools can run automatically:

- `list_workspace_files`
- `read_workspace_text`
- `search_workspace_text`
- `calculator`
- `echo`

Mutating or sensitive tools stop at an Approval boundary:

- `write_workspace_text`
- `replace_workspace_text`
- `run_workspace_command`

`run_workspace_command` executes one program directly with an argv array and `shell=False`; shell expansion is not implicitly enabled. Its working directory must resolve inside the current workspace. It supports a bounded timeout and captured stdout/stderr.

Before launching a child process, Loom removes environment variables whose names look like API keys, tokens, passwords, secrets, or private keys. The command still runs on the host and may access resources available to that process, which is why process execution remains approval-gated.

Workspace file tools mechanically reject paths that resolve outside the workspace. Exact-block replacement also fails closed if the old text is absent or matches more than one location.

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

- `/new [path]` — create a new session; reuse the current workspace or switch to another existing directory
- `/sessions` — list saved sessions and their workspaces
- `/use <session-id>` — switch sessions
- `/session` — show current session id
- `/workspace` — show the current workspace
- `/usage` — show token usage
- `/help` — show commands
- `/quit` — exit

Runtime state defaults to `~/.loom/agent_runtime/sessions/`. Set `LOOM_HOME` to change the state root. Project files remain in the selected project workspace; Loom runtime state is not stored inside the project unless you explicitly point `LOOM_HOME` there.

## Safety model

The core rule is unchanged: read-only observations can proceed automatically; file mutation and arbitrary process execution require explicit approval.

Tool handlers receive only the selected workspace as their filesystem root. Model errors and tool errors are returned as observations so the Agent can correct itself instead of pretending an action succeeded.

Command execution deliberately does not use a shell. If a workflow needs multiple commands, the model should issue multiple tool calls so each execution remains visible and approval-gated.

## Test

```powershell
python -m pytest
```

The suite covers:

- generic Agent model configuration
- complete calculator Tool Loop
- read-only recursive workspace search
- approval-gated workspace writes
- fail-closed exact replacement
- approval-gated subprocess execution
- secret environment stripping for subprocesses

GitHub Actions also runs installation, `compileall`, pytest, and a CLI smoke test on every push to `main`.

## Direction

Loom is now moving toward a true Codex-style personal Agent. The next layers are:

1. richer file diff / patch presentation
2. command permission profiles and per-session trust levels
3. Web Search
4. Browser / Computer Use
5. model selector and model settings
6. context compaction and memory
7. MCP / skills
8. sub-agents and longer task orchestration

The rule remains simple: tools expand; the safety boundary stays stable.
