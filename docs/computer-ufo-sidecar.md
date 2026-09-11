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

## Canonical model-facing path

When the mature Computer Driver layer is active, the model gets one direct desktop
task entrypoint: `computer_run_task`. `computer_status` also stays direct.

The historical Loom primitives:

- `computer_observe`
- `computer_action`
- `computer_step`

remain installed, but are `deferred` diagnostics. They can be explicitly discovered
for debugging without competing with UFO during normal task planning. `legacy` mode
preserves their historical direct exposure.

This is intentional: installing a mature GUI engine is not useful if the outer model
can randomly bypass it and re-enter Loom's old GUI-Plus loop.

## Why a sidecar

UFO pins its own OpenAI SDK, pywinauto, LangChain and related dependency graph. It
must not be installed into Loom's main `.venv`. The sidecar provides:

- a separate dependency environment;
- a hard process boundary for cancellation;
- no local TCP listener;
- a small provider-neutral event contract;
- the ability to upgrade or replace the GUI engine without changing Loom's Agent loop.

The sidecar uses local UFO `Session`, not UFO's remote WebSocket service.

## Install and preflight

Windows requires Git and Python 3.10.

From `desktop-react`:

```powershell
npm run setup:ufo
npm run preflight:ufo
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

`preflight:ufo` starts the isolated sidecar, verifies the NDJSON protocol and exact
UFO commit, and performs a clean shutdown without starting a desktop task. The
preflight child gets only an OS/network environment allowlist; provider secrets are
not forwarded for this handshake.

For a one-command development start:

```powershell
npm run dev:ready:ufo
```

## Model configuration

When a vision-capable model is selected through Loom's hot model switch, its **effective**
active connection is copied to the UFO driver in memory only. The API key is not exposed
by runtime status or Computer diagnostics. For the native OpenAI adapter, stale custom
`baseUrl` UI values are ignored consistently by both Loom and UFO.

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

## Process environment boundary

The UFO child does **not** inherit Loom's full `os.environ`. Only normal Windows/Python
runtime variables plus common proxy/certificate variables cross the process boundary.
The selected UFO provider type/base/key/model are injected explicitly.

This prevents unrelated connector tokens, additional provider keys, or application
secrets from becoming visible to the third-party GUI engine merely because they are
present in the parent Loom process.

The only Loom-specific diagnostic flag passed through is:

```text
LOOM_UFO_KEEP_RAW_LOGS=1
```

It is opt-in and contains no credential.

## Driver selection

`LOOM_COMPUTER_DRIVER` accepts:

- `auto` (default): expose the mature task boundary; prefer UFO when ready and let
  that task handler temporarily fall back to the legacy runner if UFO is unavailable.
- `ufo`: require UFO; do not silently fall back when installation/model configuration
  is missing.
- `legacy`: do not create the UFO driver and keep historical low-level tool exposure.

Use `ufo` mode for acceptance testing so a failed UFO setup can never be mistaken for
a successful legacy run.

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
into a virtual-desktop HUD point. Typed text, task text, raw provider errors and raw
stderr are not persisted in Loom's driver events.

Nested UFO action events have a balanced transcript lifecycle, but their terminal
ledger event uses a non-Computer HUD identity. Therefore Inspector can close each
action while the task-level HUD stays visible across the continuous UFO run.

Loom writes sanitized driver events into the existing per-turn Computer diagnostics
trace.

## Screenshot and raw-log privacy

UFO v3.0.8 creates request/response/evaluation writers and writes AppAgent screenshots
to its session `log_path` even when its ordinary console/Markdown logging flags are
disabled. Loom therefore adds a stronger boundary:

1. construct the UFO Session;
2. immediately rebind its `LOG_PATH` to a private temporary scratch directory;
3. replace request/response/evaluation FileWriters with in-memory null sinks;
4. remove the constructor-created `logs/<task>` directory before the task runs;
5. let screenshots exist only as transient working files in scratch;
6. delete scratch and any task directory in `finally` on completion/cancel/failure.

Abandoned Loom UFO scratch directories are cleaned on later sidecar startup after a
staleness window.

Set `LOOM_UFO_KEEP_RAW_LOGS=1` only for a deliberate raw-debug reproduction. Raw UFO
logs may contain screenshots, prompts, model responses and task content and must be
handled as sensitive data.

## Pause and cancellation

Pause/resume is implemented at the UFO command-dispatch boundary: an active task can
finish the operation already in flight but no next Windows command is dispatched
until resumed.

Control commands never auto-start a dead sidecar. This prevents a stale task id from
resurrecting a new UFO process. Local pause state changes only after the control frame
was successfully written.

Cancellation is cooperative first. If the sidecar does not return within the configured
cancel timeout, Loom terminates the isolated sidecar process. A hard sidecar kill does
not terminate Loom Desktop.

## Acceptance test

The first strict Windows acceptance scenario intentionally starts with Edge in the
foreground while WeChat is already running:

1. set `LOOM_COMPUTER_DRIVER=ufo`;
2. ask Loom to find the existing WeChat window;
3. search for `妈妈`;
4. open that conversation;
5. type `你好` into the message box;
6. stop without pressing Enter or clicking Send.

Acceptance requires that UFO refreshes/selects the current WeChat window instead of
using a stale HWND, remains anchored to WeChat even if Loom/Edge had foreground focus,
and reports selected-window-relative HUD coordinates. No raw typed text should appear
in the exported Loom Computer trace.

## Upgrade rule

Do not point the production driver at UFO `main`. Upgrade deliberately:

1. choose a release tag;
2. pin its commit SHA;
3. review Windows/UI executor and security changes;
4. run the desktop POC suite;
5. update the pin only after the suite passes.
