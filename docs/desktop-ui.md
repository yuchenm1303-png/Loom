# Loom Native Desktop v1

Loom Desktop is the first native client for the durable Loom Agent platform. It is a PySide6 application that launches and talks to `loom-app-server` over the same stdio JSON-RPC protocol documented in `docs/app-server.md`.

It does **not** embed a second Agent Runtime, model loop, permission engine, tool dispatcher, or session store.

```text
PySide6 Desktop UI
       |
stdio JSON-RPC + notifications
       |
loom-app-server
       |
Streaming Agent Runtime
       |
Tools / Browser / MCP / Skills / Code Mode / Sandbox / Memory
```

## Install

For the native client itself:

```powershell
python -m pip install -e ".[desktop]"
```

For a local development install with the existing Browser and MCP optional adapters as well:

```powershell
python -m pip install -e ".[dev,desktop,browser,mcp]"
```

PySide6 is intentionally an optional dependency so CLI/App Server installations do not need a GUI toolkit.

## Start

From a project directory:

```powershell
$env:DASHSCOPE_API_KEY="your-key"
loom-desktop
```

Or choose the initial project explicitly:

```powershell
loom-desktop --workspace C:\path\to\project --permission-mode workspace
```

The desktop client does not open a browser and does not run a localhost HTTP UI. It launches `loom-app-server` as a private child process and communicates over stdin/stdout pipes.

Provider/model selection uses the same App Server / CLI resolution rules. Omitting `--provider`, `--base-url`, `--model`, or `--permission-mode` lets the backend continue to use its existing environment/default resolution rather than duplicating those rules in the GUI.

## Module layout

The client lives in `app/desktop/`. `app/desktop_ui.py` re-exports it so
`loom_desktop` keeps one version-free import path.

| Module | Responsibility |
| --- | --- |
| `format.py` | Pure text/status/event formatting. No Qt. |
| `markdown.py` | Markdown subset parsed into rich-text and code blocks. No Qt. |
| `state.py` | `ThreadState`: the durable timeline plus live items, keyed by App Server item id. No Qt. |
| `rpc.py` | `DesktopEventBridge` and `RpcRunner`: the worker-thread/UI-thread boundary. |
| `theme.py` | Design tokens and the single application stylesheet. |
| `widgets.py` | Transcript view, message and card widgets, composer, approval card, banner. |
| `window.py` | `LoomDesktopWindow`: layout, protocol handling, conversation library. |

The three logic modules carry no toolkit dependency, so transcript ordering,
Markdown parsing and request supersession are tested without a GUI.

## Current surface

The shell contains three main areas.

### Project / Thread sidebar

- durable Thread list from `thread/list`;
- automatic reopen of an existing durable Thread;
- project/workspace chooser;
- creation of a new workspace-bound Thread;
- Thread status and workspace identity.

If no durable Thread exists in the selected Loom home, the client creates one in the launch workspace.

### Conversation workspace

The transcript is built from the ordered `turns[].items[]` timeline, so tool
calls, managed processes, workspace diffs and turn errors appear inline in the
conversation rather than only in a side panel:

- user and assistant messages, with Markdown, fenced code blocks and per-message copy;
- collapsible cards for `tool_call`, `process`, `file_edit` and `error` items;
- genuine provider-backed assistant deltas from `item/delta`;
- prompt composer that grows with its content;
- Turn interruption;
- approval card backed by `approval/requested` and `approval/respond`;
- permission mode, Turn status, workspace, and token usage.

Each row is a widget keyed by its App Server item id. A delta updates only the
widget it belongs to, so streaming cost does not grow with conversation length.
Threads whose events have been pruned, and older servers that report the
conversation only through `messages`, still render: those messages are used when
the item timeline carries no conversation of its own.

The UI may optimistically show the just-submitted user prompt, but canonical
conversation history always comes back from `thread/read`. Streaming assistant
fragments remain transient until Runtime commits the final `MODEL_RESPONSE`
atomically.

Bursts of `item/completed` notifications are coalesced into one durable re-read
rather than one `thread/read` per item, and a newer request for a given tag
supersedes an in-flight one instead of being dropped, so switching conversations
quickly cannot leave an older snapshot on screen.

Transport and conversation-management failures appear in a dismissible inline
banner. Only genuinely destructive actions (deleting a conversation) use a modal.

### Runtime inspector

The right-side tabs show protocol-backed observable activity:

- Runtime event history;
- managed process / terminal activity and output;
- latest current-Turn diff;
- Browser tool activity;
- AgentGraph control-tool activity;
- the latest process sandbox report when one exists.

The Browser and Agents tabs are intentionally honest about the current protocol boundary: App Server v1 exposes their activity through ordinary tool/runtime events, but it does not yet define dedicated live Browser-state or AgentGraph-snapshot methods. The UI does not invent those states.

## Restart and recovery

Desktop is a client of durable Threads, not the owner of them. On restart it starts a new App Server process against the same Loom home, calls `thread/list`, and reconstructs the selected Thread with `thread/read`.

Transient provider chunks and OS process handles are not treated as durable state. Interrupted Runtime recovery remains authoritative on the backend.

## Credential boundary

The desktop UI has no API-key field and does not receive provider secrets through JSON-RPC.

Credentials stay in the App Server / Runtime process and are resolved through the existing environment/provider configuration. `AppServerProcessConfig` builds process arguments only from non-secret runtime configuration; it does not copy API key values into argv, protocol metadata, widgets, or logs.

This does **not** solve the separate project-wide prompt-secret persistence gap: if a user pastes a secret into normal chat text, existing canonical transcript persistence may still store that user message. Secret/Vault and transcript sealing remain part of Phase 2.6.

## Current limitations

- This milestone is a source-install native shell, not yet a signed/installed `Loom.exe` distribution.
- Windows OS sandboxing is still not implemented; permission prompts must not be described as OS isolation.
- App Server v1 has no dedicated Browser snapshot or AgentGraph snapshot RPC yet.
- Unified PTY execution remains Phase 2.4; the Terminal tab currently reflects the existing managed-process protocol events.
- Packaging into a standalone Windows executable follows after this shell/protocol integration is stable.

## Motion

One restrained rule: newly arrived transcript rows fade in over
`MOTION_CONTENT_MS`; nothing else animates. Setting `LOOM_REDUCE_MOTION` to
`1`/`true`/`yes`/`on` makes every state change immediate.

## Test boundary

The normal test suite covers the transport client with a real subprocess speaking JSON-RPC. A dedicated `windows-desktop-smoke` CI job installs PySide6 on `windows-latest`, runs the desktop transport/UI tests with Qt's offscreen platform, and verifies the `loom-desktop` entry point.

Desktop coverage is split by dependency:

- `tests/test_desktop_state.py` and `tests/test_desktop_markdown.py` need no toolkit;
- `tests/test_desktop_rpc.py` covers the threading boundary and request supersession;
- `tests/test_desktop_transcript.py` covers widget reuse, scroll-follow and content sizing;
- `tests/test_desktop_ui.py` drives the whole window against a fake App Server.
