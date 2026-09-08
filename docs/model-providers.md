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
