# Codex Context / Compaction / Instructions Parity v1

Date: 2026-09-13

Branch: `codex-context-compaction-parity-v1`

Loom base commit: `b3dc3db50b117615be59b79806a6fd8fd38b02a8`

Implementation Codex baseline: `1715e55076737158ba61d43158ede504de6d4ce1`

Latest upstream recheck: `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`

Relevant upstream drift for this window: none. The intervening Codex commit is Windows desktop uninstall/sandbox ownership work and does not change context, compaction, or AGENTS semantics.

## Source mapping

| Concern | Codex contract | Loom port |
| --- | --- | --- |
| Context window | `session/context_window.rs` and resolved model info | resolve per model step; default auto-compact threshold is 90% of effective window |
| Auto compact trigger | active provider token usage, with configured scope | provider usage is primary; conservative estimator is fallback only |
| Auto compact scope | `total` or `body_after_prefix` | `total` supported; unsupported future non-total scope fails closed until profile/window-state owner exposes prefill state |
| Local compaction | `core/src/compact.rs` | no-tools summary request, Codex prompt, context-window oldest-item retry, cancellation without commit |
| Replacement history | real user messages under shared ~20k token budget + contextual-user summary | same observable shape; old assistant/tool suffix is not replayed |
| Summary precedence | contextual user, `SUMMARY_PREFIX + summary` | USER-role named compaction message, not SYSTEM |
| AGENTS discovery | `agents_md.rs` | root-to-cwd, one candidate per directory, override > AGENTS > fallback, shared byte budget, symlinks allowed |
| AGENTS rendering | contextual-user fragment | `# AGENTS.md instructions for <cwd>` + `<INSTRUCTIONS>...</INSTRUCTIONS>` |
| AGENTS applied lifetime | `AgentsMdManager` caches repository instructions while environment selections/trust are unchanged | process-local `AppliedInstructionCache`, keyed by resolved workspace because Loom does not yet expose a separate trust/environment selection contract here |
| Restart recovery | rollout / session reconstruction owns durable state | explicitly not owned by Window 03; no AGENTS-only durable recovery contract |

## AGENTS lifecycle

The important correction is that repository AGENTS files are **not** refreshed merely because a new turn starts. Current Codex `AgentsMdManager` reuses its applied repository snapshot while the selected environments and active-project trust level are unchanged. Step capture may call `refresh()`, but the manager returns the cached repository snapshot for an unchanged authority key.

Loom therefore keeps one in-memory applied snapshot for the resolved workspace. Editing `AGENTS.md` on disk does not silently change the applied repository instructions on the next model request or the next turn within the same runtime. A future environment/trust owner can explicitly invalidate or widen the cache key when Loom exposes those concepts.

This window does **not** persist a turn-scoped AGENTS JSON snapshot. Process restart and pending-Step recovery belong to Window 06, which must restore or reject the complete captured execution world rather than reconstructing one component independently.

Window 01 remains the owner of request/Step capture. Once the stack is integrated, a captured Step consumes the already-applied instruction snapshot; it does not define filesystem refresh policy itself.

## Context and compaction state machine

```text
prepare model request
  -> obtain applied project instructions from AGENTS cache
  -> resolve current model window and auto-compact threshold
  -> read latest surviving provider context usage
       -> provider usage unavailable? conservative fallback estimate
  -> request fits and threshold not reached? send normal request
  -> otherwise compact cloned/repaired history
       -> Codex compaction prompt as final USER message
       -> tools disabled
       -> cancellation => abort without history mutation
       -> context-window overflow => remove oldest logical input unit and retry
       -> retryable transport failure => transport retry policy
  -> build replacement history
       -> genuine user messages newest-first under shared ~20k token budget
       -> append contextual USER compaction summary
       -> do not replay pre-compaction assistant/tool suffix
  -> validate replacement against Loom transport budget
  -> atomically write Loom context checkpoint archive
  -> replace active model window
```

## Deliberate adaptations / remaining owner interfaces

- `body_after_prefix`: requires model-profile scope plus AutoCompactWindow/prefill state from the lifecycle/profile owner.
- exact pre-turn vs mid-turn placement: owned by Window 01; this PR does not modify `turn_runner.py`.
- developer-role contextual fragments: Loom's provider-neutral `AIMessage` contract does not currently expose a first-class developer role.
- TokenBudget fallback prompt/buffer and provider remote compaction v2: require provider/feature capability interfaces outside this window.
- active-context token accounting immediately after replacement: Loom currently falls back to estimation until a fresh provider usage event arrives.
- restart recovery of captured Step/world state: owned by Window 06; Window 03 intentionally supplies no AGENTS-only durable resume mechanism.

## Production files

- `app/agent_runtime/context_budget.py`
- `app/agent_runtime/context_compaction.py`
- `app/agent_runtime/context_limits.py`
- `app/agent_runtime/context_runtime.py`
- `app/agent_runtime/context_state.py`
- `app/agent_runtime/instructions.py`

No turn lifecycle core, sandbox/approval, MCP, app-server, or durable session-store architecture is modified by this window.

## Validation contract

Translated/added coverage includes AGENTS discovery/precedence/rendering, empty-primary fallback blocking, nested scope, symlink behavior, shared byte budget, applied-cache reuse for a stable environment key, explicit invalidation/environment-key separation, 90% auto-compact default, provider-usage trigger, checkpoint invalidation of stale usage, local/manual compaction, contextual-user replacement history, context-window oldest-unit retry, cancellation without commit, and fail-closed irreducible overflow.

GitHub Actions currently cannot be interpreted as an executable result. Runs observed for this branch report job failures before runner execution; jobs return no steps/logs. Therefore pytest/build/smoke status remains **unknown**, not passed and not code-failed.
