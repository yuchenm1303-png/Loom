# Persistent project completion and review continuation

Three gaps found during the November 1 review are addressed together:

* A durable program with no `currentRunId` now retries its first milestone from
  both create retry and the scheduler. The deterministic milestone operation ID
  adopts an already-created run rather than creating another one.
* A later planner's `complete: true` is a completion proposal. It freezes the full
  original objective and current program guidance into a synthetic whole-project
  candidate. A separate reviewer must execute functional evidence, cover the
  objective and pass the normal gate. Missing behavior follows normal rework;
  accepted work still follows delivery and reviewed integration. Only after
  integration does the program become completed. The verification expectation
  stays short (the tool limits it to 2,000 characters); the complete requirements
  stay in the candidate definition, without truncation.
* Each review episode receives `reviewProgress`, derived from its own candidate's
  independent session events. It names current passing checks and remaining
  criteria. The final verdict and this progress use the same evidence validator:
  failed, foreign, superseded, static, redefined or stale checks cannot be marked
  covered. Earlier reviewers' accepted scripts remain suggestions to execute,
  not proof. A continued reviewer is asked to finish remaining checks and the
  objective coverage judgment instead of restarting the entire checklist.

Evidence is advisory for continuation; the final gate revalidates it against
current files. The work does not automatically accept model claims, replay
uncertain tool calls, or change provider billing limits.

Validation includes interrupted first creation, repeated retry without duplicate
runs, independent rejection of an unsupported completion claim, completion
through normal integration, long objective preservation and seven invalid/current
evidence variants. A real-runtime continuation test records a check in one review
episode and accepts its still-current evidence in the next without another exec.
The scripted soak agent also verifies every milestone file during final objective
review. These are scripted-model checks, not a new real-model or multi-day pass.

Final-version scripted soak (two milestones, two tasks each, two workers, default
fault rates, restart every three ticks): completed after 54 ticks / 17 Host
restarts, including six simulated provider failures. All six invariants passed:
integrated files matched, reported and charged usage both 293,630 simulated
tokens, bounded/flat episode context, no active episodes and no worktrees left.
The run took 123 seconds and advanced 0.38 simulated days, not 0.38 real days.
Local report: `.cache/program-closure-final-20261101/report.json` (not committed).

Regression: 144 tests passed on an isolated checkout of `701e2863` plus this
change (program, review, checks, runner, budget, workflow, integration, fault
modes and parallel scheduling). Seven existing pytest warnings concern empty
exception-message match expressions. Four further checks passed on the shared
checkout after `a8519bfa`, including the new long-objective case, continuation,
initial-create recovery and the concurrent Git-stdin fix. No paid model call
was made for this change.
