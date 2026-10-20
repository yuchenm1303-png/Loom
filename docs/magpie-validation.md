# Magpie gateway validation

Validated on Windows, 2026-10-20, with Magpie v0.1.1297 and
`workbuddy/deepseek-v4.1-flash`. Magpie reused the local WorkBuddy desktop
sign-in via `@magpie-community/opencode-workbuddy-auth`.

## Result

Loom's existing `openai-compatible` adapter can use the independent gateway:

- Base URL: `http://127.0.0.1:3425/v1`
- Model: an exact ID from the gateway's `/v1/models` response.
- Credential: supplied through `LOOM_API_KEY`, never committed. The default
  loopback-only Magpie gateway accepts the placeholder `magpie`; a shared
  deployment requires its configured gateway key.

The live smoke test uses Loom's CLI runtime builder and default workspace
tools, a temporary workspace and a separate temporary session store. It
verified reading input, writing the sum, executing a fixed Python assertion,
remembering the previous turn, cancellation (1.3 seconds), and a successful
turn after cancellation. Only the exact verification argv and workspace are
approved; other execution requests are denied.

The host lacked the Microsoft MXC execution sandbox. The approved command
ran without OS sandbox enforcement; this result does not validate sandbox
availability or isolation.

## Compatibility fix

WorkBuddy's stream ends with an empty legacy `function_call` envelope even
after emitting modern `tool_calls`. Loom previously interpreted that envelope
as a synthetic tool call and rejected the changed ID. Empty envelopes now
contribute no fragments. Nonempty mixed protocols and malformed tool calls
remain errors. Four focused tests plus the response-protocol and execution
deadline suites passed (35 tests total).

## Reproduce

Start Magpie separately, sign in to WorkBuddy, and confirm the selected model
is exposed. Keep the gateway bound to loopback for this test. Note that
`MAGPIE_NO_ACCOUNTS=1` also hides signed-in subscription plugin models.

With a working project Python environment and `LOOM_API_KEY` set:

```powershell
python scripts/smoke_magpie.py --base-url http://127.0.0.1:3425/v1 --model workbuddy/deepseek-v4.1-flash
```

This is an opt-in live test: it sends synthetic prompts to the selected
provider and consumes account quota. It does not run as part of pytest.

The local `.venv` launcher referenced an unavailable Python installation
during this validation. The bundled Python runtime was used with the project's
existing site-packages. No environment repair was made.

## Scope and next decision

This validates Loom's CLI/runtime path, not the desktop settings flow,
automatic Magpie startup, multiple accounts, failover, images, long-running
sessions, or upstream billing. An earlier standalone exact-string response
was incorrect, so successful transport is not evidence of model quality.
Cancellation verifies Loom's local terminal state and recovery, not that
WorkBuddy immediately stops upstream generation or billing.

Keep Magpie as an independent optional gateway. A later product integration
can add connection setup, model discovery and lifecycle management. There
is currently no need to vendor Magpie's source into Loom.
