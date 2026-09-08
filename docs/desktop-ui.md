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
| `composer.py` | Prompt entry and the workspace / permission / model controls. |
| `widgets.py` | Transcript view, message and card widgets, approval card, banner. |
| `window.py` | `LoomDesktopWindow`: layout, protocol handling, conversation library. |

The three logic modules carry no toolkit dependency, so transcript ordering,
Markdown parsing and request supersession are tested without a GUI.

## Current surface

The shell contains three main areas.

### Conversation sidebar

- durable Thread list from `thread/list`, grouped by project with the current
  workspace first and the newest conversation at the top of each group;
- one line per conversation: its title, plus a status dot only for states that
  ask something of the reader (running, waiting for approval, failed). Workspace,
  status and token totals stay in the row's tooltip rather than on every row;
- search, archive view, rename/archive/delete;
- automatic reopen of an existing durable Thread.

A new conversation is a **draft**: `thread/start` is only called once the first
prompt is sent. Opening Loom, or pressing Ctrl+N and walking away, therefore
leaves nothing behind. Previously the client created a Thread eagerly, which is
why libraries filled up with empty conversations named after their folder.

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

### Composer

`app/desktop/composer.py` owns prompt entry and the decisions that apply to
what is sent. Every control is backed by something the App Server exposes:

| Control | Source | Effect |
| --- | --- | --- |
| Workspace | `thread/start` | Which project the next conversation is bound to. |
| Permission mode | `runtime.permissionModes`, `thread/start` | What Loom may do without asking. |
| Model | `runtime.model` | Which model the local App Server runs. |

The permission menu's wording is taken from the resolved snapshots in
`app/agent_runtime/permissions.py` so it cannot drift from what the runtime
enforces, and a test pins the menu to `PermissionMode`. A Thread's mode is fixed
when the App Server creates it, so an open conversation shows its own mode and a
change applies to the next one.

Protocol v1 binds a model to an App Server process, not to a Thread, so
switching model relaunches the server this window owns. Durable Threads live in
the Loom home and are reloaded afterwards; an active turn blocks the switch
rather than being discarded. A window that did not start its own server (no
`client_factory`) shows the model read-only instead of offering a choice that
would do nothing.

There is deliberately **no reasoning-effort control**. `loom_cli._build_runtime`
registers exactly one role, `AGENT_FAST_ROLE`, with one binding;
`AGENT_REASONING_ROLE` exists in `app/ai/agent.py` but is never registered, so
an effort selector would have nothing to select between. Adding one means
binding a second, reasoning-capable model first.

### Runtime inspector

The right-side tabs show protocol-backed observable activity:

- Runtime event history;
- managed processes as cards, with their command, exit status and output;
- latest current-Turn diff, coloured by hunk, addition and removal;
- Browser tool activity as cards;
- AgentGraph control-tool activity as cards;
- the latest process sandbox report when one exists.

The card tabs reuse the transcript's keyed reconciliation, so a running process
updates in place instead of the panel being rewritten as text. The panel's
minimum width is derived from its own tab bar, so the tab labels fit whatever UI
font is installed rather than being elided to "Acti… Termi…".

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
- `tests/test_desktop_composer.py` covers the composer controls and their limits;
- `tests/test_desktop_transcript.py` covers widget reuse, scroll-follow and content sizing;
- `tests/test_desktop_ui.py` drives the whole window against a fake App Server.
