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

Use a different local port when needed:

```powershell
loom-web --port 8877
```

Prevent automatic browser launch:

```powershell
loom-web --no-open-browser
```

Resume a saved session:

```powershell
loom-web --session <session-id>
```

`--session` and `--workspace` are intentionally mutually exclusive. A resumed session keeps its original workspace binding.

## What the UI exposes

The first UI version is deliberately a thin adapter over Loom's existing runtime. It provides:

- saved session list
- new workspace-bound sessions
- real user/assistant conversation history
- model/runtime status
- token usage
- tool and process activity feed
- permission-mode changes
- pending tool approval details
- Allow / Deny actions through the normal `resume_approval` path
- turn cancellation

There is no separate UI-only tool dispatcher, permission system, model loop, or session format.

## Security boundary

The UI server is local-only:

- binds to `127.0.0.1`
- rejects non-local Host headers
- rejects cross-origin POST requests
- accepts JSON mutation requests only
- sends a restrictive Content Security Policy
- does not send the model API credential to browser JavaScript
- reads canonical conversation and event state from the existing Loom session store

The UI does not change Loom's existing permission semantics. `read-only`, `approval`, `workspace`, and `full-access` behave exactly as they do in the CLI.

The broader known architectural gap still applies: if a user directly pastes a secret into a normal chat prompt, the current core Session persistence layer can persist that user message. The local UI does not claim to solve that project-wide secret-vault problem.

## Packaging direction

This browser UI is intentionally dependency-light and uses Python's local HTTP server plus bundled HTML/CSS/JavaScript. That keeps the UI easy to test now and gives a straightforward path to a later Windows executable wrapper without replacing the Agent Runtime again.
