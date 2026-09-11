# Windows Computer Driver: Microsoft UFO² sidecar

Loom's Windows Computer Use baseline is Microsoft UFO² `v3.0.8`, pinned to commit
`96983c73ed09e884a5f1d7ff8936c953b234b684`.

The goal is to stop growing a second hand-written GUI-agent kernel inside Loom.
Loom owns orchestration, permissions, HUD, Inspector and diagnostics. UFO owns the
Windows GUI-agent loop: HostAgent application selection, AppAgent execution, UIA,
window-relative visual actions and recovery.

## Architecture

```text
Loom Agent Runtime
  computer_run_task
        |
        v
ComputerDriverRuntime
        |
        v
UfoWindowsDriver  -- stdin/stdout NDJSON -->  ufo_sidecar.py
                                            (isolated Python 3.10)
                                                   |
                                                   v
                                      UFO local Session / HostAgent
                                                   |
                                      select_application_window
                                                   |
                                                   v
                                             UFO AppAgent
                                      UIA OR window coordinates
                                                   |
                                                   v
                                               Windows
```

Browser automation remains a separate browser-use path. UFO is for native Windows
applications such as WeChat, Settings, Office and other desktop software.

## Why a sidecar

UFO pins its own OpenAI SDK, pywinauto, LangChain and related dependency graph. It
must not be installed into Loom's main `.venv`. The sidecar provides:

- a separate dependency environment;
- a hard process boundary for cancellation;
- no local TCP listener;
- a small provider-neutral event contract;
- the ability to upgrade or replace the GUI engine without changing Loom's Agent loop.

The sidecar uses local UFO `Session`, not UFO's remote WebSocket service.

## Install

Windows requires Git and Python 3.10.

From `desktop-react`:

```powershell
npm run setup:ufo
```

The installer:

1. clones exactly UFO `v3.0.8`;
2. verifies the expected commit SHA;
3. creates `~/.loom/drivers/ufo/3.0.8/.venv`;
4. installs UFO's pinned requirements there;
5. creates an `agents.yaml` that references environment variables instead of storing secrets;
6. creates a Loom safety override;
7. uses a GUI-only UFO MCP allowlist (UICollector, HostUIExecutor and AppUIExecutor).

`CommandLineExecutor` and Office COM executors are intentionally excluded from the
first production baseline. They can be evaluated later as explicit capabilities.

For a one-command development start:

```powershell
npm run dev:ready:ufo
```

## Model configuration

When a vision-capable model is selected through Loom's hot model switch, its active
connection is copied to the UFO driver **in memory only**. The API key is not exposed
by runtime status or Computer diagnostics.

A dedicated UFO model can also be configured with environment variables:

```text
LOOM_UFO_API_TYPE=openai
LOOM_UFO_API_BASE=https://api.openai.com/v1
LOOM_UFO_API_KEY=...
LOOM_UFO_API_MODEL=...
```

OpenAI-compatible endpoints are supported through `API_TYPE=openai` and their base
URL. If `DASHSCOPE_API_KEY` is present and no explicit UFO settings are supplied,
Loom uses DashScope's OpenAI-compatible endpoint with `qwen-vl-max` as a compatibility
fallback.

## Driver selection

`LOOM_COMPUTER_DRIVER` accepts:

- `auto` (default): prefer UFO when installed/configured; otherwise keep the legacy
  Loom task runner as a fallback.
- `ufo`: require UFO; do not silently fall back when installation/model configuration
  is missing.
- `legacy`: do not create the UFO driver.

Low-level `computer_observe`, `computer_action` and `computer_step` remain available
for diagnostics and compatibility. `computer_run_task` is the high-level driver
boundary.

## Window anchoring and action semantics

UFO's HostAgent refreshes current desktop windows and selects an application using
current window identity before AppAgent work begins. Subsequent coordinate actions
are relative to the selected application rectangle and refocus that application
before clicking.

UIA and visual actions stay separate:

- UIA: `click_input(id, name)`
- visual fallback: `click_on_coordinates(x, y)` using application-relative fractions

Loom does not promote a visual coordinate into a different UIA target inside the UFO
path.

## HUD and diagnostics

The sidecar observes UFO's `LocalCommandDispatcher` rather than modifying UFO core.
It emits sanitized events such as:

- `task.started`
- `window.selected`
- `action.started`
- `action.completed`
- `observation.completed`
- `task.completed` / `task.failed` / `task.cancelled`

For coordinate actions the sidecar converts the selected-window-relative coordinate
into a virtual-desktop HUD point. Typed text and task text are not emitted in driver
events.

Loom writes these events into the existing per-turn Computer diagnostics trace.
UFO's own raw task log directory is deleted after each task by default. Set
`LOOM_UFO_KEEP_RAW_LOGS=1` only for a deliberate raw-debug reproduction; those logs
may include screenshots and task content.

## Pause and cancellation

Pause/resume is implemented at the UFO command-dispatch boundary: an active task can
finish the operation already in flight but no next Windows command is dispatched
until resumed.

Cancellation is cooperative first. If the sidecar does not return within the configured
cancel timeout, Loom terminates the isolated sidecar process. A hard sidecar kill does
not terminate Loom Desktop.

## Upgrade rule

Do not point the production driver at UFO `main`. Upgrade deliberately:

1. choose a release tag;
2. pin its commit SHA;
3. review Windows/UI executor and security changes;
4. run the desktop POC suite;
5. update the pin only after the suite passes.
