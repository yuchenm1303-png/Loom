# Loom Computer Use v1

Computer Use is a Runtime v2 layer, not a second autonomous agent. Loom's canonical Agent Runtime continues to own the outer model/tool loop, permissions, cancellation, durable history, limits, and recovery.

## Runtime boundary

```text
Loom Agent Runtime
  -> ComputerUseRuntime
       -> ComputerSessionStore (ephemeral desktop state/revision/trajectory)
       -> Observation: active-window screenshot + window list + UIA controls
       -> GroundingBackend: one visual-policy prediction (UI-TARS adapter when configured)
       -> WindowsOperator: UIA native action first, Win32 coordinate fallback
       -> post-action observation + verification
  -> MCP / Tool Search / Skills / Code Mode / Streaming
```

`computer_step` performs exactly one visual policy decision and at most one OS action. It never runs an internal GUIAgent loop. A later Loom model step decides whether to call it again, change strategy, use `computer_observe` + `computer_action`, or stop.

## Coordinates and DPI

All model-facing points are normalized to the current screenshot frame (`0..1`). A frame carries its physical virtual-desktop origin, size, monitor/window identity, and DPI. Windows coordinates may be negative on displays to the left/up of the primary monitor. The Windows operator enables Per-Monitor v2 DPI awareness on a best-effort basis before reading geometry.

UI-TARS output is adapted at the backend boundary. The initial adapter prompts UI-TARS-style models to emit a `0..1000` frame-local coordinate system and converts it to Loom normalized points. UI-TARS action syntax does not escape into Loom core.

## Windows execution

The optional `computer` extra provides:

- `pywinauto` for UI Automation discovery and native Invoke/Edit operations.
- `pywin32` for HWND/window management and virtual-desktop pointer input.
- `pyautogui` for keyboard shortcuts only.
- Pillow `ImageGrab` for the first screenshot provider.

The operator prefers UIA-native click/edit when a fresh `control_id` is available, then falls back to physical coordinates. Unicode text fallback uses Windows `SendInput(KEYEVENTF_UNICODE)` rather than depending on the active IME.

## Safety and persistence

Screen capture and GUI mutation tools are `sensitive`, so they pass through Loom's existing Permission/Approval engine. Screenshot bytes, UIA wrapper objects, visual-policy trajectory, and direct GUI state are process-local. `computer_observe` writes an image only when `save_screenshot=true` is explicitly requested.

Model-produced `computer_step.instruction` and `computer_action` type-text payloads are replaced with one-shot RAM references before Runtime v2 persists the model response. Raw typed text is consumed once at execution time and is not written to Session/events.

A global desktop `state_revision` makes stale targets fail closed, including cross-session interference. Each action is followed by a fresh observation. v1 verification records visual hash changes and deterministically checks `switch_window` by foreground window ID. Three identical policy actions on an unchanged screenshot trigger stuck detection before a third OS action is injected.

## Configuration

Install on Windows with:

```shell
pip install -e ".[computer]"
```

The Windows operator auto-configures when the extra is installed. A visual grounding backend is optional. Pass `computer_grounder=` explicitly, or set `LOOM_COMPUTER_MODEL_PROFILE` / `computer_model_profile=` to bind the initial UI-TARS-style one-step adapter to an already configured Loom vision-capable model profile.

Without a grounding model, Loom still exposes `computer_status`, `computer_observe`, and deterministic `computer_action`; `computer_step` is intentionally absent.

## Explicit v1 limits

- Foreground interactive Windows desktop only.
- No UAC secure desktop, lock screen, or claim of privileged/elevated control.
- No native helper or Desktop Duplication capture yet.
- UIA coverage depends on the target application; custom canvas/DirectX/remote-app surfaces fall back to coordinates and will later gain an optional OmniParser/perception backend.
- Browser DOM automation remains Loom Browser Use. Computer Use is for native desktop/chrome/dialog surfaces, not a replacement for Browser Use.
