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
- Full Python suite found eight pre-existing UI contract failures, reproduced against an independent HEAD source snapshot. The execution-lease failure was a subprocess encoding mismatch and passes with `PYTHONUTF8=1`. These UI tests were not weakened or edited in this task.
- The runtime-updater fixture lacked a newly required export; it now supplies the actual build comparator rather than an invented comparator.
- No paid model requests or live-session state changes were made. These deterministic checks establish the repaired configuration and lifecycle behavior, not a guarantee that a real model will become concise or always finish a task. Real follow-up requires a running Desktop bootstrap and Host containing this commit and checking declared working limits, compaction events, actual plan transitions, and final deliverables.
