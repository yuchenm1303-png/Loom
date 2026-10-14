# Tool call validation and recovery

The captured LongCat turn failed after tool argument schema rejections. The provider-neutral response boundary discarded the precise validation error, then TurnRunner asked only for a generic valid response. The dispatch layer used a separate validator. Invalid JSON was identified, but Python JSON decoding could silently accept duplicate keys and non-finite numbers.

The response boundary and dispatch now share a cached, offline JSON Schema validator and value-free diagnostics. Repair context includes the tool, field path, violated rule, required fields and expected types. Unknown tool errors point to currently exposed tools and tool discovery. Broken declarations are configuration errors, not repairable model responses.

Chat Completions, Responses and Messages use the same strict argument decoder. DSML parameters and raw Messages/SSE envelopes also reject duplicate properties and non-finite numbers. Valid Unicode, command quoting and split JSON content are preserved without argument coercion or shell-command rewriting.

Schema validation remains atomic before dispatch: a batch containing an invalid call executes none of its calls. Corrected calls follow the existing approval path and do not replay completed calls from earlier steps. Recoverable transport retries remain separate from bounded model-format repair. Diagnostics never include rejected argument values or unknown map keys.

Regression tests cover missing fields, wrong scalar/array types, unknown properties, composite schemas, malformed/duplicate-key JSON, NaN/Infinity/overflow, invalid declarations, all three streaming protocols, actionable repair, and execute-once behavior. These are deterministic protocol tests; they do not claim every live upstream model is always available or always generates valid calls.
