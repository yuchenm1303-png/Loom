# Loom React Desktop

This is Loom's desktop shell. It replaced the original PySide6/Qt Widgets client, which was removed once the migration finished; both spoke the same App Server JSON-RPC protocol, so nothing below the UI changed.

## Stack

- Electron: native desktop process + secure preload bridge
- React + TypeScript: renderer
- Vite: renderer dev/build pipeline
- Existing `loom_app_server.py`: Python runtime boundary over UTF-8 JSON-RPC/JSONL stdio

The Agent Runtime remains Python. The renderer never imports Python or accesses Node APIs directly.

## Run

From this directory:

```bash
npm install
npm run dev
```

Set `LOOM_PYTHON` if the desired Python executable is not `python` on Windows or `python3` elsewhere. Provider credentials remain in the existing Loom environment/credential path; do not put secrets in this frontend.

## Architecture boundary

```text
React renderer
    │ window.loom (contextBridge)
Electron preload
    │ IPC
Electron main
    │ UTF-8 JSON-RPC / JSONL over stdio
loom_app_server.py
    │
Agent Runtime / tools / providers / sessions
```

## UI principles

1. The transcript is the visual center. Side panels support it; they never compete with it.
2. Assistant prose stays flat. Tool/process activity is compact until detail is requested.
3. Disclosure motion is browser layout motion only. No screenshot/FLIP overlay and no manual pixel anchoring.
4. Streaming updates existing DOM nodes instead of recreating the transcript.
5. Runtime activity, changes and shell state live in the inspector, while important events also remain inline in the transcript.
6. Neutral dark surfaces, restrained borders, no glassy/dashboard chrome.

See `../docs/react-desktop-architecture.md` for workstream ownership and migration rules.

## Workspace styling and visual checks

`src/theme.css` owns the shared `--loom-workspace-*` palette, border, radius and
shadow tokens. `workspace-surface-refinement.css` owns transcript/card alignment
and spacing; sidebar and composer sheets consume the same material tokens.
Keep component state styling in its existing owner rather than adding another
late override sheet.

Run Vite and open `/scripts/fixtures/workspace-visual.html` for the real App with
an isolated in-memory host. Query parameters include `theme=dark`, `multiple=1`,
`empty=1`, `lang=en` and `motion=1`. The fixture never calls a real account or model.

Run `node scripts/tests/workspace-visual-browser.mjs` with Playwright Core
available. `LOOM_PLAYWRIGHT_MODULE` accepts an ESM module specifier (use a `file:///`
URL for an external Windows path), `LOOM_CHROMIUM_PATH` selects a browser, and
`LOOM_TEST_ORIGIN` selects the Vite server. Set `LOOM_WORKSPACE_SCREENSHOTS` to save
theme and interaction screenshots. The check covers native scrollbar alignment,
narrow windows, decision submission, composer controls and the English home.

`src/renderer-styles.ts` is the shared production stylesheet cascade; fixtures
must import it instead of copying a list that can drift from the application.
StreamingPresentation owns grapheme pacing and spreads final bursts over the
420ms handoff window before the process folds at 460ms. Markdown blocks enter
once during reception; later bursts update their existing DOM without replay.
The top RunProgress owns generation status, so prose has no trailing cursor.

Transient surfaces use useMotionPresence: logical closure makes retained DOM
inert, an interrupted exit reverses in place, and changing motion preferences
settles an active transition immediately. Pass an identity for different
surfaces sharing one presence owner (the composer popovers). Live sends finish
at 380ms; historical messages and persisted decision receipts stay still.

Run `node scripts/tests/runtime-motion-browser.mjs` using the same browser
environment variables above. It checks the production cascade in both themes,
stream bursts, exit/reopen races, live reduced-motion changes, preview keyboard
focus, historical/send lifetimes, IME confirmation and popover exits. Open
`/scripts/fixtures/task-flow-motion.html` to replay thinking, tool activity,
reasoning and answer handoffs, or use its film mode to inspect animation frames.
