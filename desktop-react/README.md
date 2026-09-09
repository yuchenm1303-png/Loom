# Loom React Desktop

This is the replacement desktop shell for Loom. It is intentionally isolated from the legacy PySide6/Qt Widgets desktop while migration is in progress.

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
