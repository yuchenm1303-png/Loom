# Execution-chain follow-up, 2026-10-06

## Findings and ownership

The observed MiniMax conversation declared neither its provider window nor its working context policy. Its active input continued growing despite source support for automatic compaction. The cold Desktop launch omitted `contextLimits`, and CLI bootstrap constructed `ModelBinding` without reading that metadata. Hot switching supplied the metadata through RPC. This was a configuration-path inconsistency, not a missing compaction algorithm.

`runtimeModelConfig.ts` now owns both launch argument and RPC serialization. Context metadata travels in explicit process arguments, never a previous model's inherited environment. CLI bootstrap and hot switching use the same `resolve_runtime_context_limits` implementation. Saved working-only policies are recognized without inventing a provider capacity. Cold launch also preserves the profile's vision declaration.

The plan schema previously advertised optional outcome/evidence fields while execution required them. Status-dependent JSON Schema requirements now match execution. The arbitrary eight-step ceiling is removed. Invalid evidence is reported for all milestones atomically, allowing one repair instead of repeated first-error retries. Completion remains an assistant assessment backed by referenced observations; no keyword classifier or termination counter was introduced.

The no-plan fallback appended changing execution counters and continuation reminders after every tool result. These duplicate observations already in tool history and accumulated as anchored frames. This input-only fallback is removed. Durable tool receipts and real plan state remain; text is not filtered, rewritten, or matched against keywords.

## Verification and limitations

- Cold-start metadata and plan-schema gaps were reproduced with failing tests before changes. Restoring the HEAD implementations in an isolated test process reproduced both failures after the fix, without changing the working tree.
- A production CLI-created runtime test exercises the real context-budget/compaction/checkpoint path with accumulated anchored frames, preserves archived canonical history and latest state, and uses a deterministic local summary response.
- Existing working-context tests cover provider usage calibration, unknown model metadata, soft versus hard budgets, and oversized indivisible input.
- Focused execution-chain regression suite: 86 passed. Electron suite: 62 passed. TypeScript checks passed.
- The initial full Python sweep found eight pre-existing UI contract failures, reproduced against an independent HEAD source snapshot. The execution-lease failure was a subprocess encoding mismatch and passes with `PYTHONUTF8=1`. The initial cold-start fix left these UI tests unchanged; the follow-up below corrects their obsolete assertions while preserving the behavior checks.
- The runtime-updater fixture lacked a newly required export; it now supplies the actual build comparator rather than an invented comparator.
- No paid model requests or live-session state changes were made. These deterministic checks establish the repaired configuration and lifecycle behavior, not a guarantee that a real model will become concise or always finish a task. Real follow-up requires a running Desktop bootstrap and Host containing this commit and checking declared working limits, compaction events, actual plan transitions, and final deliverables.

## Follow-up: capabilities and honest capacity reporting

The same session exposed a second independent failure: schema-pressure selection silently removed direct tools as history grew. The model then called capabilities it had already used, producing unavailable-tool observations. Schema planning now compacts descriptions and annotations only; it preserves every direct capability and validating schema. Unified context preparation receives the retained schema cost and compacts history. Tool search exclusively activates tools explicitly declared deferred; the pressure-shedding state and recovery path are removed, rather than extended with preferred names or keyword rules. Existing search and sensitive-binding tests now use explicitly deferred fixtures, preserving their activation and authorization assertions.

Unknown provider capacity previously displayed a guessed fallback denominator as 100%. Context reports now distinguish declared model capacity, explicit working budgets, observed input bounds, and unknown capacity. Unknown capacity displays measured tokens without a fabricated percentage or free-space count. The view also handles old Host reports with fallback capacities honestly. Automatic-compaction policy remains independent of that display.

New failing regressions reproduced disappearing direct tools and the fabricated full meter before the source changes. A CLI-created production runtime compacts accumulated history, checkpoints it, and then successfully executes a direct tool, retaining capabilities in every execution request. Browser checks cover the real meter in dark/light themes, unknown/known capacity and compaction progress. These checks use local deterministic responses, not paid model calls or changes to the user's live session.

### Release-gate contract corrections

The eight independently reproduced existing UI failures are corrected without removing checks or changing production UI:

- Composer import order checks base/refinement/geometry relative ordering rather than requiring those files to be the final imports. The existing 14px geometry assertion remains.
- Composer decoration checks reflect the base surface's removed glow and verify both retained decorations are hidden by the refinement; steering accessibility checks remain.
- Sidebar and home ownership permit shared glyph motion only after validating every matching rule's properties and glyph target. Dimensions, placement, typography and surfaces still require their original owner; new negative tests reject geometry overrides and container movement.
- Extension update checks follow the extracted shared install helper and additionally require code copying before the reload signal. Existing connected-browser behavior remains checked.
- Transcript artifact checks require an explicit preview button and forbid an automatic embedded rendering surface, matching the requested non-disruptive preview design. The right-side renderer and event wiring remain checked.
- Dock external-open checks cover both selected artifacts and library entries with workspace binding, replacing the obsolete single-path variable assertion.
- Thread cache checks follow terminal-error invalidation through the current active-ID reference and require invalidation before clearing turn activity; size, TTL, running-state and freshness checks remain unchanged.

The full sweep also exposed an intermittent missing first-run initialize response. Transport inspection found an independently reproducible EOF race: a finite worker join was followed by stopping the writer even if accepted RPCs were still running. Orderly EOF now drains accepted requests before draining responses, rather than declaring transport completion while a daemon worker still owns a request. A deterministic delayed-request test fails against the old implementation and verifies both queued replies after the fix; no delay threshold was increased. Bootstrap assertions now retain exit/stdout/stderr diagnostics instead of failing with an uninformative `StopIteration`. A disconnected client remains subject to normal pipe write failures; EOF draining does not cancel an accepted control-plane request merely because its processing takes more than five seconds.

Final validation: a clean full Python rerun passed (five platform/optional skips); the subsequently added EOF regression and all 32 bootstrap/stdio/server checks passed separately. The final tree collects 2,216 tests. Electron's 62 checks, both TypeScript projects, renderer/Desktop build, four meter-model checks and real dark/light browser checks passed. Isolated restoration of the old tool planner, capacity report and EOF method made their new regressions fail again. The user's running session and model account were not used for these checks.
