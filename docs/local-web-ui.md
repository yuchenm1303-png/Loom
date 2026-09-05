# Loom Local Web UI

Loom includes a local browser UI for testing the real Agent Runtime without working directly in the terminal.

## Start

Install Loom normally, then run:

```powershell
loom-web --workspace C:\path\to\your-project --permission-mode workspace
```

The launcher binds only to `127.0.0.1` and opens:

```text
http://127.0.0.1:8765/
```

Use `--port` to choose another local port, `--no-open-browser` to prevent automatic launch, or `--session <session-id>` to resume a saved thread. `--session` and `--workspace` are intentionally mutually exclusive because a resumed session keeps its original workspace binding.

## What the UI exposes

The Web UI remains a thin adapter over Loom's existing runtime. It provides saved sessions, workspace-bound session creation, real conversation history, model/runtime status, token usage, tool/process activity, permission-mode changes, pending approvals, Allow/Deny, cancellation, and Phase 2.2 partial assistant text.

There is no separate UI-only tool dispatcher, permission system, model loop, or session format.

## Phase 2.2 streaming behavior

The configured top-level Agent Runtime now consumes real provider streams. While a model step is active, the local Web service exposes the public assistant text accumulated so far as a presentation-only partial message.

The current browser transport still samples the localhost snapshot endpoint approximately every 450 ms while busy, so the browser may coalesce several provider chunks into one visual update. The source data is genuine provider streaming; rich App Server clients receive the same Runtime stream directly as notifications without this polling layer.

Partial Web messages are never written to `session.json` or `events.jsonl`. The final assistant message appears only after Runtime has reconstructed and validated the full response and durably committed the canonical `MODEL_RESPONSE`.

Private reasoning/provider hidden reasoning fields are not exposed.

## Security boundary

The UI server is local-only:

- binds to `127.0.0.1`;
- rejects non-local Host headers;
- rejects cross-origin POST requests;
- accepts JSON mutation requests only;
- sends a restrictive Content Security Policy;
- does not send the model API credential to browser JavaScript;
- reads canonical conversation/event state from the existing Loom session store.

The UI does not change Loom's permission semantics. `read-only`, `approval`, `workspace`, and `full-access` behave exactly as they do in the Runtime.

The known project-wide gap still applies: if a user directly pastes a secret into a normal chat prompt, the current Session persistence layer can persist that user message. Streaming does not solve the later Secret/Vault problem.

## Packaging direction

This browser UI remains the dependency-light debug/fallback client. Phase 2.3 will add the native Windows desktop shell on top of `loom-app-server`, without replacing the Agent Runtime.
