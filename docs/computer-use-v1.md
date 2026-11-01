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

Three rules keep a control action landing once, on the control the model saw (adapted from agent-desktop's Windows adapter):

- Every execution carries `delivery`: `not_delivered`, `delivered` or `uncertain`. The physical fallback runs only after a native write that provably never reached the target (no pattern, element gone or disabled, worker busy). A native Invoke or SetValue that outlives its deadline is `uncertain` — a button that opens a modal dialog blocks Invoke until the dialog closes — so the action stops there and asks for a fresh observation instead of pressing the control a second time.
- A `control_id` is a position in one UIA walk. Before acting, the stored wrapper is re-read and compared on control type, automation id and label (not on value-bearing names of Edit/Document/ComboBox; geometry only for a control with neither id nor label). A changed or vanished control refuses the action before any input; a re-read that cannot finish in 500 ms sets the wrapper aside and uses the screenshot point.
- Anonymous layout containers (no name, no automation id) are left out of the control shortlist: Pane/ToolBar/Window everywhere, plus Group/Custom in Chromium/Electron windows (`Chrome_WidgetWin_*`), where they are page scaffolding. Named containers stay. `switch_window` refuses a window id whose handle now belongs to a different process (`pid` + process creation time recorded per observation).

## Single-loop speed: batches and Set-of-Mark

The single-loop path (the default) spends ~90% of a Computer Use turn waiting on the model (`scripts/computer_use_report.py`, 2026-11-01 baseline: 6.0 s per model step, 0.79 desktop actions per call). Two changes cut round trips, following OSWorld-Human's latency breakdown and UFO2's speculative multi-action execution:

- `computer_action` takes `actions`, up to six steps planned from one screenshot (click a field and type, fill a form, press Enter). Everything is validated before the first input. Between steps the batch stops, leaving the rest unsent and attaching a fresh screenshot, when the previous step failed or its effect could not be confirmed (including `delivery: uncertain`), when the foreground window changed or moved, when the patch around the next pointer target no longer matches the planned screenshot (2.5% of the frame, a pixel counts at a 16-level difference, the patch at 25%), or when a step is refused. Each step goes through the same refusals, repeat block and effect classification as a single action. The result carries `batch: {requested, executed, stopped_reason, stopped_at, steps}`; typed text inside a batch is stashed like a single action's.
- The model-facing screenshot carries Set-of-Mark: each UIA hint is boxed in magenta with its number, and `control_id` accepts that number (`17`, `[17]` or `uia:17`) for click, move, type and clear_text; Loom acts on the control's real center. Only the model's copy is marked; verification compares the untouched captures. `LOOM_COMPUTER_SET_OF_MARK=0` turns it off. The full accessibility tree is never serialized into the prompt (OSWorld-Human measured 3–26 s per tree and thousands of tokens per step).

Measure before and after with `python scripts/computer_use_report.py --since <date>`: actions per call, model share of time, failure share and how batches ended.

## What the live suite changed (2026-11-01)

`scripts/computer_use_suite/` runs nine tasks against throwaway local apps with a real vision model and the real Windows operator, and checks each from facts the app wrote: tkinter forms, a settings dialog, a form covered by another window, a native Save As dialog, a long list, a dense 80-seat grid, copying a code between two windows, renaming (select-all and replace), and a Chromium form in Edge (temporary profile) with a dropdown, radios, a number and a date field. It drives the real mouse and keyboard. Every change below came from reading what a run actually did (`digest.py`).

Per step, the provider takes 3–6 s to its first chunk even with 80–95% of the input cached; uncached input is 2–3k tokens and output is small. So step time is the provider's, and the lever Loom holds is the number of steps. What cost steps, and the fix:

- Clicks into an empty field or onto a checkbox read as "no observable change" (a caret or a tick vanishes in the 128x72 sample), ending batches after their first step. Now: a full-resolution check at the target and across its row, one second look after 0.35 s, and a full-resolution changed-pixel count for small changes elsewhere (a status label).
- Mid-change captures (a dialog half open, a long path half typed) made the model "wait" or look again. Now a visible change is observed again every 0.3 s until two captures agree, at most 1.2 s.
- Models copied a handle or placeholder from their own history into new `type` actions. The outgoing request now shows the model its own earlier text from RAM; the session keeps handles; handles stay one-shot.
- A covered small window fell off the window list the model sees (the screenshot is the foreground window only, the list was sorted by area). Now Z-order, with early slots for both covered and hidden windows; `switch_window` accepts an exact title or process name.
- `clear_text` pressed Escape whenever the IME state was unknown (normal with TSF input methods) and closed the Save dialog it was clearing a field in. Escape is now sent only for a composition that was seen and survived the IMM cancel.
- Call shapes models commonly send are accepted instead of rejected: `actions` nested in `action`, a top-level `frame_id`, a trailing `screenshot` in a batch, a negative scroll amount. Typed text is stashed in any shape (a nested batch had put literal text into history).
- Hints cover every walked control (up to 80) in short lines, and `control_id` also takes a hint's exact name: cut at 40, half a dense grid had no number; with 80 numbers the model spent 34 s working out which one was "F7".
- New read-only views, attached once beside the screenshot: `zoom {region}` (full-resolution crop, up to 4x) and `screenshot {window_id}` for a window behind the foreground one (PrintWindow, no activation).

Results with deepseek-v4.1-flash, all tasks passing throughout: baseline 611 s and 76 model steps with 9 failed calls for the nine tasks; after the first fixes 442 s, 60 steps, 3 failed calls (mean of two runs per task). Run-to-run variance per task is large (one task ranged 24–68 s), so compare means over repeats.

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
