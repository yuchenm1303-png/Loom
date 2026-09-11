# Windows Computer Driver: Microsoft UFO² sidecar

Loom's Windows Computer Use baseline is Microsoft UFO² `v3.0.8`, pinned to commit
`96983c73ed09e884a5f1d7ff8936c953b234b684`.

The product goal is that users see one built-in Computer Use capability, not a UFO
setup workflow. Loom owns orchestration, permissions, HUD, Inspector and diagnostics.
UFO owns the Windows GUI-agent loop: HostAgent application selection, AppAgent
execution, UIA, window-relative visual actions and recovery.

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

## Built-in capability contract

The model-facing desktop API is intentionally small:

- `computer_run_task` is the normal desktop task entrypoint.
- `computer_status` reports readiness and setup state.
- `computer_observe`, `computer_action` and `computer_step` remain installed as
  `deferred` diagnostics, but they no longer compete with UFO during normal task
  planning.

This is the product boundary: users ask Loom to operate a desktop app; Loom selects
and starts the driver. Users should not need to know UFO internals, provide a second
model name, or choose low-level screenshot/action tools.

## Why a sidecar

UFO pins its own OpenAI SDK, pywinauto, LangChain and related dependency graph. It
must not be installed into Loom's main `.venv`. The sidecar provides:

- a separate dependency environment;
- a hard process boundary for cancellation;
- no local TCP listener;
- a small provider-neutral event contract;
- the ability to upgrade or replace the GUI engine without changing Loom's Agent loop.

The sidecar uses local UFO `Session`, not UFO's remote WebSocket service.

## Main development startup

`main` is the active test/development line for the Windows Computer Driver. The normal
startup command now uses the strict UFO driver path by default:

```powershell
npm run dev:ready
```

That command auto-provisions the driver on Windows, runs the UFO preflight before
Electron starts, and refuses to fall back to the old legacy GUI loop when UFO setup is
broken. This prevents a failed UFO setup from being mistaken for a successful legacy
Computer Use run.

For debugging only, the old auto/fallback launcher is still available as:

```powershell
npm run dev:ready:auto
```

Manual setup remains available for debugging:

```powershell
npm run setup:ufo
npm run ufo:preflight
```

The installer:

1. clones exactly UFO `v3.0.8`;
2. verifies the expected commit SHA;
3. finds Python 3.10 if it already exists;
4. otherwise downloads the official Python `3.10.11` Windows x86-64 installer into
   `~/.loom/cache/python/3.10.11`, verifies the Python.org MD5 checksum, and installs
   a private Python runtime into `~/.loom/runtimes/python/3.10.11`;
5. falls back to `winget install Python.Python.3.10` only if the private runtime
   bootstrap is unavailable or blocked;
6. creates `~/.loom/drivers/ufo/3.0.8.venv` from the resolved Python 3.10;
7. installs UFO's pinned requirements there;
8. creates an `agents.yaml` that references runtime environment variables instead of
   storing secrets;
9. creates a Loom safety override;
10. writes the UFO MCP allowlist (UICollector, HostUIExecutor, AppUIExecutor and
    CommandLineExecutor) into `config/ufo/mcp.yaml`, keeping the upstream file as
    `mcp.yaml.ufo-original`.

`LOOM_UFO_AUTO_INSTALL_PYTHON=0` disables automatic Python installation, and
`LOOM_UFO_BOOTSTRAP_PYTHON` can point to a specific Python 3.10 executable.
`LOOM_PYTHON_RUNTIME_ROOT` can override the private Python runtime directory.

### Why the allowlist has to overwrite `mcp.yaml`

UFO v3.0.8 declares `MCP_SERVERS_CONFIG` but never reads it, and its `UFO_ENV`
override loader merges `mcp_loom.yaml` at the top level instead of nesting it under
the `mcp` key the way it nests the base file. Both routes therefore leave the stock
server list in force. Loom writes the allowlist into `config/ufo/mcp.yaml` because
that is the file UFO actually loads.

### What the allowlist does and does not bound

`CommandLineExecutor` is included. Its `run_shell` tool name is misleading: UFO
validates the base command against a fixed allow-list of application launchers
(notepad, calc, explorer, browsers, Office, code), rejects roughly twenty dangerous
patterns, and spawns without `shell=True`. Launching a target application
deterministically is faster and more reliable than making the visual agent find it
in the Start menu, which is the point of combining deterministic actions with
vision rather than insisting on vision alone.

Office and PDF COM executors stay excluded. Driving document object models is a
different capability class and deserves its own evaluation.

None of this is an execution boundary. A computer-use agent that can see a terminal
window can type into it, and the permitted browser and explorer launchers accept
arbitrary URLs and paths. Treat the allowlist as capability shaping, not as a
sandbox.

`ufo:preflight` starts the isolated sidecar, verifies the NDJSON protocol and exact
UFO commit, verifies Python 3.10 and all three Loom config files, and performs a
clean shutdown without starting a desktop task. The
preflight child gets only an OS/network environment allowlist; provider secrets are
not forwarded for this handshake.

Startup output is deliberately staged as `source`, `python-runtime`, `venv`,
`dependencies`, `config`, `ufo-preflight`, and `dev-ready`. A failure exits before
Electron and names the failed boundary (for example `python.org download blocked`,
`installer checksum mismatch`, `private Python install failed`, `UFO venv creation
failed`, `pip install requirements failed`, or `sidecar preflight failed`).

`computer_status.task_driver` exposes `source_installed`, `venv_installed`,
`dependencies_installed`, `config_installed`, `preflight_ready`, `ready`, and a
specific `reason`, so an incomplete installation is never reduced to one ambiguous
`installed: false` flag.

## Model configuration

Normal users should not configure a separate UFO model. Loom attaches RAM-only metadata
to the active model platform, and `ComputerDriverRuntime` copies that effective
vision-capable provider/model/key into the UFO driver before every status check or
desktop task. This includes both the initial app-server model and hot-switched models.

For the native OpenAI adapter, stale custom `baseUrl` UI values are ignored consistently
by both Loom and UFO. For OpenAI-compatible providers, `/chat/completions` and
`/responses` suffixes are normalized back to the provider base URL.

A dedicated UFO override is still available for deliberate debugging or benchmarking:

```text
LOOM_UFO_API_TYPE=openai
LOOM_UFO_API_BASE=https://api.openai.com/v1
LOOM_UFO_API_KEY=...
LOOM_UFO_API_MODEL=...
```

If no active Loom vision model can be inherited and no UFO override is supplied, strict
`ufo` mode reports the missing model/provider as a setup error instead of silently
running the legacy GUI loop.

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

- `ufo`: require UFO; do not silently fall back when installation/model configuration
  is missing.
- `auto`: expose the mature task boundary, prefer UFO when ready and let that task
  handler temporarily fall back to the legacy runner if UFO is unavailable.
- `legacy`: do not create the UFO driver and keep historical low-level tool exposure.

On `main`, `npm run dev:ready` forces `ufo`. Use `dev:ready:auto` only when debugging
legacy fallback behavior.

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

## Acceptance scenario

The first Windows scenario intentionally starts with Edge in the foreground while
WeChat is already running:

1. start main with `npm run dev:ready`;
2. ask Loom to find the existing WeChat window;
3. search for `妈妈`;
4. open that conversation;
5. type `你好` into the message box;
6. stop without pressing Enter or clicking Send.

Success requires that UFO refreshes/selects the current WeChat window instead of using
a stale HWND, remains anchored to WeChat even if Loom/Edge had foreground focus, and
reports selected-window-relative HUD coordinates. No raw typed text should appear in
the exported Loom Computer trace.

## Upgrade rule

Do not point the production driver at UFO `main`. Upgrade deliberately:

1. choose a release tag;
2. pin its commit SHA;
3. review Windows/UI executor and security changes;
4. run the desktop POC suite;
5. update the pin only after the suite passes.
