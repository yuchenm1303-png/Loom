# Model providers and switching

Loom separates the Agent role from the provider/model that executes it. The runtime continues to target the stable `agent.fast` role; changing models replaces the provider/model binding behind that role rather than changing the Agent execution loop.

## Desktop saved models

Open the model control below the composer and choose **Add API / model…**.

A saved connection contains only non-secret metadata:

- a display name;
- API type (`openai` or `openai-compatible`);
- Base URL for OpenAI-compatible endpoints;
- the provider's model ID;
- an OS credential-store alias.

The API key is stored through the operating-system credential store and is not written to `models.json`. Model metadata lives at `<LOOM_HOME>/models.json` (normally `~/.loom/models.json`).

After a connection is saved, it appears in the model menu. Selecting it restarts the local App Server with that connection's provider, endpoint, model and credential. Durable conversations remain on disk and are reloaded after the restart. Loom blocks model switching while a turn is active.

The last successfully selected saved connection becomes the default on the next desktop launch unless explicit `--provider`, `--base-url` or `--model` arguments are supplied.

## What “generic API” means

The generic adapter is **OpenAI-compatible Chat Completions**. Any gateway or model service that implements the OpenAI Chat Completions request/stream/tool-call contract can be entered as `Base URL + API key + model` without Loom-specific provider code.

For normal Agent operation, the selected endpoint/model must support text generation, streaming and function/tool calling. An HTTP API with a completely different wire protocol is not automatically compatible; it needs a dedicated Loom provider adapter. Native Anthropic and Gemini adapter slots already exist in the provider catalog but remain deliberately non-executable until those protocol adapters are implemented and tested.

## Legacy CLI configuration

The existing one-provider launch path remains supported:

- `--provider openai --model <model>`
- `--provider openai-compatible --base-url <url> --model <model>`

Environment-based credentials continue to work as before. Saved desktop connections are an additional configuration layer, not a replacement for the CLI path.


## OpenCode Console Free vs OpenCode Go

Loom exposes **OpenCode Go** and **OpenCode Console** as separate built-in
provider groups and separate account-gateway routes. They must never share
endpoints, model selection IDs or API keys.

- **Go**: `builtin:opencode-go:<model>` goes to
  `https://opencode.ai/zen/go/v1`, and consumes the Go plan allowance.
- **Console**: `builtin:opencode-console:<model>` goes to
  `https://opencode.ai/inference/openai/v1`. The documented free
  OpenAI-compatible chat models, including `deepseek-v4.1-flash-free`, are
  listed as $0 in OpenCode's pricing table. **However, the live API returns
  HTTP 403 / `FreeTierError` for third-party clients**, including Loom:
  "OpenCode's free tier can only be used from within OpenCode." Pricing is not
  an API entitlement. Loom shows these models as restricted (nonselectable),
  and its gateway rejects free requests locally rather than trying to
  impersonate the official client or consuming Go quota. Use the official
  OpenCode app for these free models.
- Both built-in groups still require a Loom sign-in and must pass Loom's model
  policy (provider group, global model, and per-user permissions).
  Passing Loom's account policy does not override OpenCode's separate
  free-tier client restriction.
- A Console service-account API key is needed for **paid** Console models.
  Configure `LOOM_OPENCODE_CONSOLE_API_KEY` or `OPENCODE_CONSOLE_API_KEY`
  **on the model-gateway and catalog-policy services**, never in the frontend.
  This key must be issued by Console, *not* Go. Free models are still sent
  upstream without this paid credential even if the key is configured.
- Supplying a Console service key does not bypass this restriction for free
  models; do not attempt to forge client identity headers.
- With a Console service key, the policy service discovers compatible models
  from the official `https://opencode.ai/console/api/v2/config` workspace
  configuration; it does **not** query the Go `/models` endpoint. It excludes
  non-Chat-Completions providers and endpoint-specific incompatible models.
  Newly discovered metered models default to **OFF** in Loom Admin. An
  administrator must explicitly enable them before Loom users can invoke
  billed calls. If the Console key is removed, only the verified free
  catalogue remains available.
- A user can still use **Add personal connection** and their own Console key
  through the generic OpenAI-compatible adapter if desired. Personal
  connections use local OS credential storage and are separate from the
  shared built-in gateway/account policy. For Console custom connections,
  use `https://opencode.ai/inference/openai/v1` and the model's actual
  provider ID, not the Loom `builtin:` selection.
- The Console `/inference/openai/v1` path serves Chat Completions; other
  provider protocol families (Responses, Anthropic, Gemini, SystemOne) are not
  automatically supported by this built-in group.

References: https://opencode.ai/v2/docs/console/api/inference/ and
https://opencode.ai/v2/docs/console/api/ and
https://opencode.ai/v2/docs/console/models/
