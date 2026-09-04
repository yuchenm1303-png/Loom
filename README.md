# Loom

Loom is a personal, general-purpose AI agent built for real tool use: durable sessions, model/tool loops, explicit approvals, workspace isolation, cancellation, recovery, and observable execution events.

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
- Per-session isolated workspace
- Token usage accounting
- Safe built-in tools: calculator, echo, workspace list/read
- Approval-gated workspace write tool
- Independent Codex-style terminal interface

Private model chain-of-thought is neither requested nor persisted. Loom stores only observable messages, tool activity, lifecycle events, usage, and workspace state.

## Install

Python 3.11+ is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Or install the plain requirements:

```powershell
python -m pip install -r requirements.txt
```

## Run with the existing DashScope setup

If you already use the same DashScope environment as the original Agent harness:

```powershell
$env:DASHSCOPE_API_KEY="your-key"
loom
```

`AI_API_KEY` is also recognized for compatibility. With a DashScope key and no explicit endpoint/model, Loom defaults to:

- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Model: `qwen-plus`

You can override the model:

```powershell
$env:LOOM_MODEL="qwen-plus"
loom
```

## Run with any OpenAI-compatible endpoint

```powershell
$env:LOOM_API_KEY="your-key"
$env:LOOM_BASE_URL="https://your-provider.example/v1"
$env:LOOM_MODEL="your-model"
loom --provider openai-compatible
```

For native OpenAI:

```powershell
$env:OPENAI_API_KEY="your-key"
$env:LOOM_MODEL="your-model"
loom --provider openai
```

Secrets are resolved at runtime and are not written into session snapshots or repository configuration.

## CLI

Interactive mode:

```powershell
loom
```

One-shot task:

```powershell
loom "Use the calculator to compute 123 * 456"
```

Resume a saved session:

```powershell
loom --session <session-id>
```

Useful interactive commands:

- `/new` — create a new session
- `/sessions` — list saved sessions
- `/use <session-id>` — switch sessions
- `/session` — show current session id
- `/workspace` — show the current isolated workspace
- `/usage` — show token usage
- `/help` — show commands
- `/quit` — exit

Runtime state defaults to `~/.loom/agent_runtime/sessions/`. Set `LOOM_HOME` to change the root.

## Safety model

Read-only tools can run automatically. Mutating or sensitive tools do not bypass the harness: they stop the turn at an approval boundary and resume only after an explicit user decision.

The initial write tool can only resolve paths inside the current session workspace. Attempts to escape the workspace are rejected mechanically.

## Test

```powershell
python -m pytest
```

The bootstrap tests cover the generic model configuration, a complete calculator tool loop, and approval-gated workspace mutation without making a real API request.

## Direction

Loom is intentionally no longer a commerce agent. The next layers should be general-purpose capabilities built on the same runtime contract:

1. Model settings and model selector
2. Web search
3. Browser / Computer Use
4. Shell and command execution with explicit permission policy
5. Rich file editing and diff workflow
6. Better task/session UI
7. Memory and context compaction
8. MCP / skills
9. Sub-agents and longer-running task orchestration

The rule is simple: tools expand; the core safety boundary stays stable.
