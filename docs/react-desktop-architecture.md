# React Desktop Architecture and Collaboration Plan

Branch: `feat/react-desktop-migration`

The migration replaces only the desktop presentation layer. Python Runtime, App Server, provider adapters, tools, MCP, Browser Use, Computer Use, process runtime, permissions and persisted sessions remain authoritative.

## Product layout

The first stable shell is a three-zone Agent workspace:

```text
┌───────────────┬────────────────────────────────────┬──────────────────┐
│ Conversations │ Thread / Transcript                │ Runtime          │
│               │                                    │                  │
│ New thread    │ Thought / Assistant / Tool flow    │ Activity         │
│ Search        │                                    │ Changes          │
│ Thread list   │                                    │ Shell            │
│               │ Composer                           │                  │
└───────────────┴────────────────────────────────────┴──────────────────┘
```

### Left: conversation library

- New conversation
- Search
- Active conversations
- Later: archive/restore, rename, context menu
- Keep metadata quiet; title first, state only when meaningful

### Center: working surface

- Header: thread title + workspace + minimal run state
- Transcript: max reading width around 820 px
- User messages: compact right-aligned bubbles
- Assistant messages: flat document-style prose
- Thought process: subordinate inline disclosure above its answer
- Tool/process/diff: compact activity row with detail disclosure
- Approval: explicit inline action surface
- Composer: fixed to the bottom of the working surface, not the window

### Right: Runtime inspector

- Activity: tool/process lifecycle
- Changes: file edits/diffs
- Shell: processes/output
- Inspector is optional and collapsible
- It must never own canonical state; it is another projection of App Server events

## Motion contract

The new renderer must not reproduce the Qt geometry architecture.

- Disclosure header stays in normal DOM flow and is never manually repositioned.
- Disclosure body uses normal browser layout; the rows after it are naturally displaced.
- No viewport screenshots, FLIP snapshots, scroll-position correction loop or animation-time geometry cache.
- Do not animate `top/left` for transcript rows.
- Prefer CSS layout/compositor properties and short, restrained transitions.
- Keep `scrollbar-gutter: stable` on the transcript scroller.
- Respect `prefers-reduced-motion`.
- Streaming content must not force a React remount of stable transcript items.

## Runtime boundary

Electron main owns the Python child process and JSON-RPC request ids. The renderer sees only the typed preload API:

- `connect()`
- `call(method, params)`
- `onNotification(listener)`
- `disconnect()`

Current App Server methods already cover the first migration target:

- `runtime/status`
- `thread/start`, `thread/resume`, `thread/list`, `thread/read`, `thread/fork`
- `thread/rename`, `thread/archive`, `thread/delete`
- `turn/start`, `turn/steer`, `turn/interrupt`
- `approval/respond`

Notifications include thread, turn, item, approval and resync events. Streaming assistant/tool/process deltas are merged in renderer state by stable item id.

## Workstream ownership for parallel windows

All parallel work should branch from `feat/react-desktop-migration`, not `main`, and avoid overlapping the same directories unless coordinated.

### A — Shell / design system (owner: primary window)

Paths:
- `desktop-react/src/App.tsx`
- `desktop-react/src/styles.css`
- shared shell structure and design tokens

Responsibilities:
- global information architecture
- spacing/type/color tokens
- responsive shell
- interaction consistency
- final integration decisions

### B — Transcript / Markdown / Thought

Paths:
- `desktop-react/src/components/Transcript.tsx`
- future `desktop-react/src/components/transcript/*`

Responsibilities:
- assistant Markdown/code rendering
- reasoning split/presentation
- disclosure motion
- copy actions
- long-thread rendering/virtualization only when measurement proves it is needed

Must not change Electron RPC transport.

### C — Conversation library

Paths:
- `desktop-react/src/components/Sidebar.tsx`
- future `desktop-react/src/components/sidebar/*`

Responsibilities:
- thread search
- rename/archive/delete
- context menus
- empty/loading states

### D — Runtime inspector

Paths:
- `desktop-react/src/components/Inspector.tsx`
- future `desktop-react/src/components/inspector/*`

Responsibilities:
- activity timeline
- diff view
- shell/process view
- inspector tabs and filters

### E — Composer / permissions / models / attachments

Paths:
- `desktop-react/src/components/Composer.tsx`
- future `desktop-react/src/components/composer/*`

Responsibilities:
- input growth
- send/stop/steer
- permission mode picker
- model picker using existing saved-profile model APIs once exposed to App Server
- file/image attachment UX when protocol support is ready

### F — Electron / App Server bridge

Paths:
- `desktop-react/electron/*`
- `desktop-react/src/state/*`
- `desktop-react/src/types/*`

Responsibilities:
- process lifecycle
- JSON-RPC robustness
- notification reducer
- reconnect/resync
- typed protocol contracts
- packaging startup path

Must not redesign visual surfaces.

## Merge discipline

1. Never modify legacy `app/desktop/*` for the React migration.
2. Keep Python business logic out of the renderer.
3. Never expose provider credentials through preload or renderer state.
4. App Server remains the only Runtime boundary.
5. Each parallel window should make focused commits and state its touched paths before handoff.
6. The primary window integrates cross-cutting changes and resolves design-system conflicts.
7. Do not merge this migration into `main` until the replacement shell passes an end-to-end parity checklist.

## Definition of replacement-ready

- starts/stops the Python App Server reliably
- lists, opens and creates threads
- streams assistant text without transcript remount/flicker
- renders tool/process/file-edit items
- approvals work
- interrupt works
- Thought disclosure is stable and smooth
- runtime inspector tracks the same events as inline transcript
- thread management works
- model/permission controls are functional
- app restarts and reloads persisted threads
- keyboard, scrolling and long-thread behavior are verified on Windows
- packaging smoke test succeeds

Until then, the Qt desktop remains the fallback and is not deleted.
