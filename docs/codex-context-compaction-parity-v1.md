# Codex Context / Compaction / Instructions Parity v1

Date: 2026-09-13

Branch: `codex-context-compaction-parity-v1`

Loom base commit: `b3dc3db50b117615be59b79806a6fd8fd38b02a8`

Codex baseline: `1715e55076737158ba61d43158ede504de6d4ce1`

The Codex baseline was re-read from `openai/codex` main at the start of this work. It was still the requested 2026-09-13 commit, including the current direct-tool metadata / compaction changes.

## Source mapping

| Concern | Codex source / contract | Loom counterpart | Port decision |
| --- | --- | --- | --- |
| Canonical model history | `codex-rs/core/src/context_manager/history.rs` stores model-window items separately from retained host facts; `for_prompt()` is projection | `AgentSession.messages` plus Loom checkpoint store | Keep durable archive separate from model replacement history; compaction no longer treats the checkpoint archive as the prompt window |
| Durable rollout reconstruction | `codex-rs/core/src/session/rollout_reconstruction.rs`, `codex-rs/rollout/src/model_context.rs` reconstruct from durable rollout/checkpoints rather than treating raw rollout as the next prompt | `ContextCheckpointStore` + session snapshot | Preserve Loom durable checkpoint product data, but render reconstructed summary at contextual-user precedence |
| Context window | `codex-rs/core/src/session/context_window.rs` resolves the active model step's window | `context_limits.py` | Resolve every model step; model/profile changes immediately change the threshold |
| Auto compact limit | current `ModelInfo::auto_compact_token_limit()` default is 90% of resolved context window | old Loom default was 78% of local input budget | Use 90% of effective model window; explicit model limit still wins |
| Auto compact trigger | `session/turn.rs` primarily uses current token usage and active model window | old Loom used UTF-8 bytes / 3 as the normal trigger | Use latest provider-reported response usage when available; fallback estimate only when provider usage is absent |
| Manual compact task | `tasks/compact.rs`, `tasks/mod.rs`, `core/src/compact.rs` model compaction as a task with cancellation | `compact_context_with_model()` | Keep Loom public API, but use the same no-tools compaction prompt / replacement semantics and propagate cancellation before commit |
| Context-window retry | `core/src/compact.rs` retries `ContextWindowExceeded` after dropping the oldest input item | old Loom had three semantic summary retries plus custom reducers | Drop oldest logical history item; keep tool call/output group together in Loom's message representation; remove semantic finish-reason retry policy |
| Compact output replacement | `core/src/compact.rs::build_compacted_history` keeps real user messages newest-first under ~20k tokens and appends `SUMMARY_PREFIX + summary` as contextual user history | old Loom used SYSTEM summary + arbitrary recent suffix | Port Codex shape: real user messages + contextual-user summary; do not replay pre-compact assistant/tool suffix |
| Compact prompt | `codex-rs/protocol/src/prompts/templates/compact/prompt.md` | old Loom custom language-aware SYSTEM prompt | Use current Codex prompt as the final user message of the compaction request |
| Summary prefix | `codex-rs/prompts/templates/compact/summary_prefix.md` | old Loom `LOOM_CONTEXT_CHECKPOINT` SYSTEM wrapper | Use current Codex prefix and USER role |
| Direct tool metadata | current main strips disabled direct metadata from inference and compaction clones, without mutating durable history | Loom `AIMessage` has no direct-execution metadata; approval binding lives in `pending_bindings`, outside messages | No destructive strip needed: execution binding is structurally absent from model history. Preserve durable binding state |
| AGENTS discovery | `codex-rs/core/src/agents_md.rs` | `instructions.py` | Root -> cwd, one candidate per directory: override > AGENTS > configured fallback, shared 32 KiB budget |
| AGENTS scope | base prompt + `agents_md` tests: directory subtree scope, deeper file wins conflicts | old Loom flattened files into a SYSTEM message | Preserve root-to-cwd ordering and contextual-user role so deeper rules appear later without gaining system precedence |
| Base instructions | config selects explicit base override, then `model_instructions_file`, then configured instructions | Loom session system prompt | Preserve base SYSTEM prompt; do not mix project docs into it |
| Developer instructions | `core/src/context/developer_instructions.rs` uses a developer contextual fragment | Loom `AIMessage` has no developer role | Documented platform adaptation; no out-of-scope `app/ai` contract rewrite in this window |

## Instruction precedence

Observable target hierarchy:

```text
base/system instructions
        >
developer instructions
        >
direct user instructions
        >
project docs / AGENTS scope chain
    repo/root AGENTS
        < deeper AGENTS
        < cwd/deepest AGENTS
```

Within each directory the first existing candidate wins:

```text
AGENTS.override.md
    > AGENTS.md
    > configured fallback names
```

The root-to-cwd project-doc chain is rendered as contextual user content. It is not promoted to SYSTEM. Loom's existing runtime-state and communication-language product context remain transient system context; compaction never rewrites them into durable conversation history.

## Context and compaction state machine

```text
prepare model step
  -> resolve current model window / explicit compact threshold
  -> project transient base + Loom product context + project docs + canonical model window
  -> read latest surviving provider token usage
       -> checkpoint newer than usage? invalidate old usage
       -> no provider usage? conservative fallback estimate
  -> below threshold and hard transport budget? send normal request
  -> otherwise compact cloned/repaired history
       -> final user message = Codex compact prompt
       -> tools disabled
       -> cancellation => abort without history mutation
       -> context-window overflow => remove oldest logical input unit and retry
       -> other retryable transport failure => transport retry policy
       -> summary text (empty => Codex-compatible placeholder)
  -> build replacement model history
       -> genuine user messages, newest first, shared ~20k token cap
       -> append contextual USER compaction summary
       -> no old assistant/tool suffix replay
  -> validate replacement against Loom Chat Completions transport budget
  -> atomically write Loom durable checkpoint archive
  -> replace active session model window
  -> next model step re-injects transient instructions at the same precedence
```

## Removed / simplified Loom behavior

The following behavior was removed from the active context path because it had no current Codex equivalent and could alter observable semantics:

- default 78% local-input auto-compact threshold;
- normal-path tool-output collapsing solely to make a prompt fit;
- emergency request-visible truncation of user messages;
- arbitrary `keep_recent` assistant/tool suffix replay after compaction;
- SYSTEM-role compaction summary;
- Loom-specific language-aware compaction SYSTEM prompt;
- three-attempt semantic summary completeness loop based on finish reason / unexpected verbosity.

`keep_recent` remains accepted by the public Loom methods for API compatibility, but it no longer changes Codex-parity replacement history.

## Loom-only context retained

These product capabilities remain intentionally intact and transient so they do not pollute Codex core history semantics:

- authoritative Loom runtime/world-state envelope;
- durable checkpoint archive for inspection/recovery;
- durable goals and queue state surfaced in runtime context;
- communication-language product guidance;
- Memory / Browser / Computer / multi-agent product layers above the context runtime.

## Deliberate adaptations / unresolved ownership boundaries

### Developer role

Codex has a distinct developer contextual fragment. Loom's `AIMessage` contract currently exposes only SYSTEM/USER/ASSISTANT/TOOL. This window therefore maps AGENTS/project docs to USER messages with a stable name and leaves base instructions as SYSTEM. Adding a first-class developer role would require changing the provider-neutral AI contract outside this window's production write ownership.

Risk: a backend that treats named USER content differently from Codex contextual-user fragments may not be byte-for-byte identical, but project docs no longer gain the stronger SYSTEM precedence they had before.

### Exact pre-turn / mid-turn lifecycle placement

Codex distinguishes pre-turn compaction from mid-turn rollover and only performs mid-turn rollover when a follow-up model step is required. Loom invokes context preparation immediately before each model request from the shared turn runner. This window did not modify the turn lifecycle core because that file belongs to window 01.

Current behavior is close at the model-request boundary, but exact parity for "compact before recording a new turn input" and the Codex mid-turn `needs_follow_up` gate requires a small lifecycle hook owned by window 01.

### Active token accounting

Codex stores active-window token usage and recomputes it after replacement history. Loom's existing `AgentSession.usage` is cumulative billing/goal usage and must not be repurposed. This port reads the newest durable per-response provider usage event; a newer checkpoint invalidates that value and the next request temporarily uses the conservative estimator until fresh provider usage arrives.

Risk: immediately after compaction, one request can use fallback accounting rather than provider-native active-window accounting. Solving this exactly requires a dedicated active-context usage field or lifecycle callback outside the current ownership boundary.

### Chat Completions output reservation

Loom must reserve `max_output_tokens` locally for stateless Chat Completions-compatible providers. Therefore a hard request-budget check can trigger before the pure 90% auto-compact threshold. This is a platform transport constraint, not an alternative compaction state machine.

### Direct tool-call metadata

Codex main at the recorded SHA strips disabled direct-call execution metadata from model inference and compaction clones. Loom's message contract has no equivalent metadata field. Execution/approval identity is held in `pending_bindings`, not serialized into `AIMessage`, and existing execution-binding projection tests verify prompt-only schema changes do not change approval identity. No durable binding information is deleted by this port.

## Tests translated / added

- nested AGENTS root-to-cwd ordering;
- conflicting AGENTS with `AGENTS.override.md` precedence;
- cwd-only behavior when root markers are disabled;
- configured fallback instruction names;
- shared instruction byte budget;
- discovered AGENTS symlink behavior;
- 90% auto-compact default and model-window changes;
- provider usage as primary compact trigger;
- checkpoint invalidation of stale provider usage;
- manual compact no-tools request and exact compact prompt placement;
- replacement history contains real user messages + contextual summary only;
- tool call/output pair remains complete in durable archive;
- context-window retry removes the oldest logical tool group from the compact request clone;
- cancellation does not commit compaction;
- irreducible giant user input fails closed rather than silent truncation;
- fixed instruction/tool-schema pressure fails closed;
- reconstructed checkpoint summary keeps contextual-user precedence;
- durable checkpoint archive remains distinct from model projection.

## Changed production files

- `app/agent_runtime/context_budget.py`
- `app/agent_runtime/context_compaction.py` (new)
- `app/agent_runtime/context_limits.py`
- `app/agent_runtime/context_runtime.py`
- `app/agent_runtime/context_state.py`
- `app/agent_runtime/instructions.py`

No runtime/turn lifecycle core, sandbox/approval, MCP, app-server, or durable-store architecture file was modified.
