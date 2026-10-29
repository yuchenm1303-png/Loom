# Project runner robustness: from fixes to one mechanism

Real-model runs kept finding new ways a correct episode could stop a whole
project: a step limit, a network drop, a reply followed by text, reviewer files
in its checkout, a scope declared too narrowly. Each was fixed where it hit, but
they shared four roots. This pass addresses the roots.

## 1. The runtime records what happened; the runner reads fields

Before, the runner parsed message text: `"tool call limit reached"`,
`"APIConnectionError"`, the final text including the runtime's appended
verification note.

* `limit_reached` records `limit`: `model_steps`, `tool_calls`, `no_progress`,
  `context`, `admission`.
* `turn_failed` records `provider_failure` (an `AITransportError` or
  `ModelRequestTimeout`) next to `retryable` and `provider_status_code`.
* `turn_completed` already kept `model_text` apart from `verification_notice`;
  the runner now reads the reply from `model_text`.

Message text is read only for events written before these fields existed.

## 2. One recovery policy (`app/project_runner/runner.py`)

`episode_outcome()` classifies an ended episode; `RECOVERY` maps each
recoverable kind to its counter, allowance and event; `_recover()` applies it.

| kind | what happened | next | allowance |
| --- | --- | --- | --- |
| budget | a request was refused at the token budget | `budget_limited`; add budget to continue | - |
| episode_bound | step or tool limit of one episode | next episode continues | 7 |
| no_progress | the runtime's loop guard stopped a repeated failing operation | next episode, told to change approach | 2 |
| provider_transient | provider/network failed, retryable | wait 30 s doubling to 30 min | 10 |
| review_evidence | a verdict without accepted evidence | back to the reviewer with the reason | 2 |
| reply_format | a final reply the runner cannot read | back to the model with the reason | 2 |
| scope | a team task changed another task's files | back to the worker with the paths | 2 |

One more occurrence than the allowance stops the run for inspection; any
accepted episode resets every counter. Permanent provider refusals (401-404,
`retryable: false`), a worker's deliberate `blocked` and genuine review
failures still stop or rework as before. Run fields keep their earlier names,
so runs already in a store continue.

## 3. Sandbox compatibility before any model call

`python -m app.agent_runtime.sandbox_compat ROOT` runs ordinary development
operations through the production launch path in about a minute: files,
nested and unicode paths, 0o700 directories, `tempfile`, child processes,
`unittest`, scripts in this and an earlier turn's scratch, git, node, cmd,
PowerShell. It also checks the boundaries: writes outside the workspace, reads
of ungranted host files and network connections must fail. The qualification
preflight runs it and stops before reading credentials on any failure.

Git needs the elevated host preparation (`scripts/windows-sandbox-prep.ps1`,
run by the user); without it the git case is a warning naming that step. On
this machine today: 17 pass, git warns, all three boundaries hold.

## 4. Scripted tests replay real failure modes

`tests/test_project_fault_modes.py` runs one two-task project through a reply
without JSON, a provider connection error, a reply followed by the runtime's
note, reviewer output left in its checkout across episodes, and a pass filed
with static evidence. It must still pass with exactly one recovery of each
kind and never stop for inspection. Reverting strict reply parsing or either
whole-checkout staleness comparison fails it.

## What this does not prove

These are scripted and zero-model checks. Real-model qualification, long runs
and installed-desktop acceptance are recorded separately in
`project-runner-live-qualification-2026-10-28.md`.

## Found by real ledger run 7 (after the consolidation)

The consolidated runner recovered every protocol failure on its own (a provider
outage, an under-evidenced verdict), so the run reached its 5M token budget
instead of stopping. Where the tokens went exposed two more defects:

* **Scratch was per turn.** A project task spans many episodes (turns), so
  every boundary handed the model an empty `scratch:` folder; one reviewer
  spent nine episodes (1.6M tokens) recreating probe scripts and data files.
  Project sessions now keep one scratch folder per session
  (`runtime.session_scoped_scratch`); ordinary conversations stay per turn.
* **Failed requests were assumed at the largest request.** Eighteen connection
  drops and timeouts were charged about 40k each (724k assumed). A request that
  failed before any output chunk arrived is now assumed at its own input
  estimate; with output already streaming, or no timing recorded, it stays at
  the largest request seen.

## Borrowed from mature long-running harnesses

Studied: Anthropic's [Effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents)
and its autonomous-coding quickstart prompts, Cursor's
[Scaling long-running autonomous coding](https://cursor.com/blog/scaling-agents), and the
OpenHands [stuck detector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector) and
condenser. What Loom already matched: an immutable feature/criteria list (only status
changes; Loom binds criteria by definition hash), fresh context per cycle (episode
boundary compaction), a planner/worker/judge split with the judge deciding each cycle,
and few coordination roles (no integrator).

Adopted in this pass:

* **Accepted checks are kept and re-run** (`app/project_runner/checks.py`), the role of the
  feature list's test steps and the session-start regression check. When an independent
  review passes, the commands it cited (and scratch scripts they ran) are stored per
  project; later reviews get them as `acceptedChecks` to re-run first and then probe what
  they miss, and workers get earlier tasks' checks as `regressionChecks` to run before new
  work. Only reviewer-cited passing checks are kept; a later reviewer still runs and
  records everything in its own session.
* **Project notes** (`project_notes`), the role of `claude-progress.txt`: a final reply may
  carry up to five short `notes` (environment facts, pitfalls); later episodes of the
  project see the newest thirty as `projectNotes`.
* **A stuck turn gets a corrective step** (`no_progress` in the recovery table), as the
  OpenHands detector intervenes rather than only halting.
* **Boundaries carry the layout, not an order to re-read everything**: the compaction
  summary lists workspace files with sizes and asks for targeted reads; the qualification
  harness no longer caps episodes at 8 steps (production has no step cap; a README task
  spent five of every eight steps re-orienting and never finished).
