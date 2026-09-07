# Loom Computer Use v1

Computer Use is a Runtime v2 layer, not a second autonomous agent. Loom's canonical Agent Runtime continues to own the outer model/tool loop, permissions, cancellation, durable history, limits, and recovery.

## Runtime boundary

```text
Loom Agent Runtime
  -> ComputerUseRuntime
       -> ComputerSessionStore (ephemeral desktop state/revision/trajectory)
       -> Observation: active-window screenshot + window list + UIA controls
       -> GroundingBackend: one visual-policy prediction
            -> Alibaba GUI-Plus adapter when a runtime DashScope key is configured
            -> UI-TARS-style adapter when an explicit Loom model profile is configured
       -> WindowsOperator: UIA native action first, Win32 coordinate fallback
       -> post-action observation + verification
  -> MCP / Tool Search / Skills / Code Mode / Streaming
```

`computer_step` performs exactly one visual policy decision and at most one Loom GUI action. It never runs a second autonomous GUIAgent loop. A later Loom model step decides whether to call it again, change strategy, use `computer_observe` + `computer_action`, or stop.

## Coordinates and DPI

All Loom model-facing points are normalized to the current screenshot frame (`0..1`). A frame carries its physical virtual-desktop origin, size, monitor/window identity, and DPI. Windows coordinates may be negative on displays to the left/up of the primary monitor. The Windows operator enables Per-Monitor v2 DPI awareness on a best-effort basis before reading geometry.

Provider-specific coordinates are adapted only at the grounding boundary. Both the UI-TARS adapter and Alibaba GUI-Plus adapter use a `0..1000` frame-local model coordinate space and convert it to Loom normalized points before any OS action is accepted. Provider syntax never escapes into Loom core.

## Windows execution

The optional `computer` extra provides:

- `pywinauto` for UI Automation discovery and native Invoke/Edit operations.
- `pywin32` for HWND/window management and virtual-desktop pointer input.
- `pyautogui` for keyboard shortcuts only.
- Pillow `ImageGrab` for the first screenshot provider.

The operator prefers UIA-native click/edit when a fresh `control_id` is available, then falls back to physical coordinates. Unicode text fallback uses Windows `SendInput(KEYEVENTF_UNICODE)` rather than depending on the active IME.

## Alibaba GUI-Plus grounding

Loom supports Alibaba Cloud Model Studio GUI-Plus as a dedicated Computer Grounding backend. The default model is `gui-plus-2026-02-26` and the default OpenAI-compatible endpoint is the existing Beijing DashScope endpoint. Alibaba recommends its newer workspace-specific Beijing domain for higher stability; set `LOOM_COMPUTER_BASE_URL` to that endpoint when a Workspace ID is available.

The adapter uses the provider's screenshot + `<tool_call>` protocol, requests high-resolution image processing by default, parses exactly one `computer_use` action, validates the result, and converts it into Loom's provider-neutral `ComputerPrediction`. Provider-specific XML, request options, and `0..1000` coordinates remain isolated in `computer_alibaba.py`.

For a Windows source install:

```shell
pip install -e ".[computer]"
```

Configure the credential only in the process environment, never in Loom config or session files:

```shell
DASHSCOPE_API_KEY=<your-runtime-key>
```

Optional non-secret/runtime settings:

```shell
LOOM_COMPUTER_GROUNDER=alibaba-gui-plus
LOOM_COMPUTER_MODEL=gui-plus-2026-02-26
LOOM_COMPUTER_BASE_URL=https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LOOM_COMPUTER_HIGH_RES=true
LOOM_COMPUTER_ENABLE_THINKING=false
```

`LOOM_COMPUTER_API_KEY` can be used instead of `DASHSCOPE_API_KEY` when the GUI model should have a dedicated credential. It takes precedence over the general DashScope key. If neither grounder nor model-profile setting is explicit, a runtime `LOOM_COMPUTER_API_KEY`/`DASHSCOPE_API_KEY` automatically selects GUI-Plus when the Windows Computer operator is available.

The App Server/Desktop child inherits environment variables from its parent process. The credential is therefore not placed on the command line and is not serialized into `AgentSession`, SQLite, durable events, Computer Use status, or screenshot metadata.

## UI-TARS compatibility

The earlier one-step UI-TARS-style adapter remains supported. Pass `computer_grounder=` explicitly, or configure `LOOM_COMPUTER_MODEL_PROFILE` / `computer_model_profile=` with an already configured Loom vision-capable model profile. `LOOM_COMPUTER_GROUNDER=ui-tars` can be used to make the choice explicit.

Without a grounding model, Loom still exposes `computer_status`, `computer_observe`, and deterministic `computer_action`; `computer_step` is intentionally absent.

## Safety and persistence

Screen capture and GUI mutation tools are `sensitive`, so they pass through Loom's existing Permission/Approval engine. Screenshot bytes, UIA wrapper objects, visual-policy trajectory, and direct GUI state are process-local. `computer_observe` writes an image only when `save_screenshot=true` is explicitly requested.

Model-produced `computer_step.instruction` and `computer_action` type-text payloads are replaced with one-shot RAM references before Runtime v2 persists the model response. Raw typed text is consumed once at execution time and is not written to Session/events.

A global desktop `state_revision` makes stale targets fail closed, including cross-session interference. Each action is followed by a fresh observation. v1 verification records visual hash changes and deterministically checks `switch_window` by foreground window ID. Three identical policy actions on an unchanged screenshot trigger stuck detection before a third OS action is injected.

## Explicit v1 limits

- Foreground interactive Windows desktop only.
- No UAC secure desktop, lock screen, or claim of privileged/elevated control.
- No native helper or Desktop Duplication capture yet.
- Windows has no complete OS-level sandbox yet; Computer Use permissions are not an OS isolation boundary.
- UIA coverage depends on the target application; custom canvas/DirectX/remote-app surfaces fall back to coordinates and can later gain a separate perception backend.
- Browser DOM automation remains Loom Browser Use. Computer Use is for native desktop/chrome/dialog surfaces, not a replacement for Browser Use.
