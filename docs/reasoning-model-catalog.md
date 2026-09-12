# Reasoning model catalogs

Loom can read reasoning metadata from a Codex-compatible model catalog instead of hard-coding every third-party model in Python.

By default Loom looks for:

```text
~/.loom/models.json
```

Set `LOOM_MODEL_CATALOG_JSON` to use another file:

```powershell
$env:LOOM_MODEL_CATALOG_JSON="C:\path\to\models.json"
```

The reasoning fields intentionally follow the Codex `models.json` shape:

```json
{
  "models": [
    {
      "slug": "example-reasoner",
      "default_reasoning_level": "high",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Fast responses with lighter reasoning"},
        {"effort": "high", "description": "Deeper reasoning"},
        {"effort": "max", "description": "Maximum reasoning depth"}
      ]
    }
  ]
}
```

Namespaced relay ids such as `provider/example-reasoner` match the catalog slug `example-reasoner`.

Loom also accepts the older field spelling `default_reasoning_effort` / `supported_reasoning_efforts`. Unknown future effort strings are preserved so a provider catalog can add a normal reasoning level without requiring a Loom source-code release.

Catalog parsing is cached by file path and on-disk signature. Repeated model metadata lookups do not re-read an unchanged file, while replacing or editing the file automatically invalidates the cache. Malformed catalogs still fail closed and are retried after their file signature changes.

Provider-native reasoning protocols take precedence over generic catalog metadata. For example, MiniMax M3 keeps its native Direct / Adaptive thinking contract even if a `models.json` entry for `MiniMax-M3` incorrectly advertises OpenAI-style effort levels. This prevents an external catalog from changing the wire protocol for a model Loom already knows requires provider-specific parameters.

`max` is treated as an advanced choice and requires an explicit selection in the desktop UI. `ultra` and `persistent` are currently filtered from external catalogs because Codex assigns them product/runtime semantics that Loom's Chat Completions transport does not yet implement completely. They should not be exposed as ordinary provider request values.

If the catalog is missing or invalid, Loom fails closed: it falls back to bundled provider knowledge and does not invent reasoning support for an unknown model.

## DeepSeek V4

Loom includes a built-in fallback matching DeepSeek's current official Codex catalog:

- `deepseek-v4-flash`: Low / High / Max, default High
- `deepseek-v4-pro`: Low / High / Max, default High
- `deepseek-v4-flash-vision-exp`: Low / High / Max, default High

The same rules apply to namespaced ids such as `deepseek/deepseek-v4-pro`.

For DeepSeek V4 Chat Completions, Loom also preserves the provider-private `reasoning_content` required for tool-call continuity. When a request contains tools, prior assistant reasoning state is replayed exactly as required by the provider. It is not emitted through Loom's public model-response events or streaming UI events.

That continuation state can be stored inside the local `session.json` snapshot so a resumed thread can continue using tools correctly. It is stored under the internal `_provider_reasoning_content` key and Loom's atomic runtime JSON writes use user-only file permissions where the operating system supports them. The same protection applies to temporary atomic files and crash-recovery journal state, so provider-private reasoning is not briefly written through a more permissive staging file. Loom does not treat this provider state as user-visible assistant text, memory, or audit-event content.
