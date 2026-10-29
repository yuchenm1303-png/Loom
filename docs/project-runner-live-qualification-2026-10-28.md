# Real-model qualification: attempted, not passed

The new opt-in `scripts/smoke_project_runner.py` creates a **new** isolated Git
repository and separate Host state, using a selected configured model credential
resolved from the OS credential store. It runs the production project service,
existing runtime and automatic local integration, with explicit token, episode,
per-turn model/tool and wall-time limits. It does not reuse or modify Loom's
working checkout. Reports contain identifiers, status, usage counters and bounded
failure categories, not provider response bodies or credentials. Raw durable
execution state remains local in the chosen qualification directory.
Before reading credentials or calling a model, the current harness requires a
usable workspace sandbox and exercises the selected Python executable in it.
Development `PYTHONPATH` is not forwarded into qualification commands: otherwise
the DACL backend can treat the whole developer checkout as a tool read dependency.

The test objective is a standard-library Python integer-list CLI. Following local
integration, a fixed external oracle runs eight valid/invalid input checks; the
model cannot rewrite that oracle. A plausible constant-output implementation is
explicitly rejected by the oracle's regression tests. Success requires both the
project workflow and the external oracle to pass. Approvals, blocked states,
budget limits and deadlines stop the attempt rather than silently widening
permissions or budgets. Closing/cancellation reads the current run revision under
the actual scheduler lock, so a concurrent terminal/usage event cannot turn a
provider failure into an unrelated stale-revision cleanup error.
The harness additionally observes real model-response usage and cancels following
requests after its threshold. Already in-flight requests can still exceed that
threshold; this is not a hard provider billing cap. Production project token
budgets remain between-episode soft limits. Late terminal usage is reconciled
without dispatching another episode, with unsettled episode counts reported.

Example (the directory must not already exist):

```powershell
python scripts/smoke_project_runner.py --stored-model <configured-model-id> `
  --provider opencode-go --root .cache/project-live-new-attempt `
  --seconds 480 --token-budget 80000 --max-episodes 12
```

On Windows, set `LOOM_WINDOWS_SANDBOX_EXECUTABLE` to the installed MXC launcher
when it is not discoverable on PATH. Preflight failure stops before paid calls;
the harness never silently switches to unrestricted execution.

Use the configured model's normal adapter by omitting `--provider`. OpenCode's
dedicated transport can be selected explicitly; the CLI now accepts the same
`opencode-go` adapter already supported by its runtime resolver. The transport
preserves Loom's coding-agent identity/session headers and model-specific routing.
The OpenCode configuration uses `deepseek-v4.1-flash` and the Go endpoint from
[the official integration documentation](https://opencode.ai/docs/go/).
The explicit test transport does not change the user's active model selection.

Observed on 2026-10-28:

* The existing DeepSeek profile returned HTTP 402 `Insufficient Balance` during
  planning. There was no valid planner output or implementation. The first harness
  version then hit a stale-revision cancellation race; that harness bug is fixed
  and reproduced by a scheduler/cleanup concurrency regression test.
* At the user's request, an OpenCode Flash test profile was added with its secret
  stored in the OS keyring, outside Git. The real Go request returned HTTP 401
  `api_key_expired`, before any valid planner output. This attempt reported a
  blocked authentication outcome and successfully requested durable cancellation.
  It was not retried blindly and is **not a successful live project test**.
* With the user's renewed OpenCode credential, real planning succeeded. The next
  implementation step required approval because the temporary CLI environment
  did not discover the installed MXC launcher. It was stopped, not approved by
  widening permissions. Its reconciled known usage is 28,209 tokens.
* A subsequent attempt explicitly used the installed MXC backend, with workspace
  and network isolation. A real planner produced a one-task plan and the worker
  created `tally.py`. Executions encountered missing PATH aliases, slow/failed
  command launches and a DACL mutex timeout on the development checkout. No final
  candidate or successful full workflow was obtained; the worker reached its
  model-step limit after approximately 476 seconds. Later sandbox probes also
  timed out. The underlying native sandbox issue is not claimed to be resolved.
* That attempt exposed a production accounting bug: the runtime's durable
  `LIMIT_REACHED` event was omitted from project terminal recognition, so it was
  incorrectly called unknown and only the planner's 6,324 tokens appeared in the
  initial report. The adapter now recognizes model/tool limits as settled known
  failures. A zero-model-call reconciliation of the real retained events charged
  128,216 tokens exactly once, leaving no unsettled episode. This exceeded the
  configured 80,000 between-episode soft threshold within one worker episode;
  it must not be described as a hard cap. The current harness request-boundary
  guard prevents continuing unnoticed through further requests in future tests.
* No real-model acceptance pass, source integration, parallel-model qualification,
  or multi-day reliability result has been obtained. The renewed credential works;
  the current blocker is native sandbox execution/preflight. No further paid
  requests were sent after identifying that blocker.

Local artifacts are retained under `.cache/project-live-20261028-first` and
`.cache/project-live-20261028-opencode`. They are not committed. Regression tests
exercise the fixed oracle, credential environment restoration, redacted reporting,
provider-failure classification, cancellation under concurrent revision updates,
and the full failed driver lifecycle with an offline failing provider. Those tests
do not substitute for a successful real-model run or installed desktop acceptance.
Additional artifacts are in `.cache/project-live-20261028-opencode-renewed` and
the user's `.loom/qualification/20261028-opencode-sandbox` directory. Their separate
`usage-reconciled.json` files preserve the original reports rather than rewriting
the history. Total known reported usage across the two authenticated planning/
implementation attempts is 156,425 tokens; this is token accounting, not a dollar
or provider-quota billing assertion.

## Attempt with production request-boundary budget (2026-10-28, after cf30847d)

Zero-model preflight passed first (windows-mxc `appcontainer-dacl`, workspace +
network isolation, sandboxed runtime Python in 2.7-3.6 s) after the MXC probe was
allowed to finish its ACL recovery; see `project-runner-request-budget-2026-10-28.md`.
One bounded real run, `.cache/project-live-20261028-budget` (80,000 tokens, 12
episodes, 480 s):

* Planner: one task, 9,198 tokens. Worker: first sample 7,685 tokens, then a
  real `exec` of the runtime Python **inside the MXC sandbox succeeded**
  (`sandbox=windows-mxc:workspace`, exit 0, 4.5 s) - the first real sandboxed
  command execution in these attempts.
- The worker's next model request failed with `APITimeoutError` after ~21 s
  (about the Windows TCP connect timeout; the harness sets 0 transport
  retries). The run became `needs_review`, then was cancelled. No candidate, no
  oracle run: **not a pass**.
* Production ledger: 16,883 known tokens charged once (matches the script's
  independent count); the failed request counted as 1 unknown request with an
  assumed 9,198, not as known usage. No unsettled episode.
* Cumulative known usage across authenticated attempts: 156,425 + 16,883 =
  173,308 tokens. No further request was sent after this transport failure.

Open: transport retries happen inside one `model_executor.execute` call, so the
budget admits the logical request once and counts a failed attempt inside it
only as part of that request. The next attempt should decide whether the
harness allows one transport retry for connection timeouts.

## Runs 2-7 and what each one fixed (2026-10-28/29)

Same OpenCode Flash profile, same isolated tally objective and external oracle;
every run in a new `.cache/project-live-*` directory, sandbox preflight first.
Token figures are the production ledger's known usage (cached input included).

| Run | Cap | Known tokens | Reached | Stopped by | Fix (commit) |
| --- | --- | ---: | --- | --- | --- |
| budget2 | 80k | 32,857 | worker wrote tally.py | two ~21 s connect timeouts via the local TUN proxy (198.18.x fake-ip) | harness uses production retry defaults |
| budget3 | 80k | 67,716 | worker ran checks in MXC | 16-tool-call episode bound treated as failure | episode limits continue in the next episode; exec accepts empty arguments (be22f576) |
| budget4 | 300k | 288,935 | worker finished, all self-checks pass | budget (refused at the boundary, not exceeded); 3 steps lost to scratch denied in sandbox | exec gets the turn scratch (319183e0) |
| run5 | 500k | 443,125 | task review ran all 5 checks | budget; 45,218 double-charged as unknown; review denied its previous turn's scratch | tool-contract responses count once; session-wide scratch (6fd088c2) |
| run6 | 1M | 955,158 | review in its 4th episode | budget; requests grew 8k -> 61k: continued episodes re-sent all history | episode-boundary compaction from run state, no model call (f8e2b346) |
| run7 | 1M | 676,212 | **independent task and whole-project acceptance passed** | delivery export: autocrlf=true turned the reviewed LF file into CRLF | runner checkouts keep exact bytes; fast-forward checks the delivered tree (a643bab7) |
| run8 | 1M | 618,003 | task acceptance passed; whole-project reviewer verified all criteria and reported passed | runner refused the pass (one check filed as check_kind=static) and the run stopped | an under-evidenced verdict goes back to the reviewer with the named criterion and reason (2 tries); prompt states static never counts |

The production budget held in every run (no run exceeded its cap; refusals
happened at request boundaries). After compaction the largest request stayed
at ~27k. Total known real usage across runs 1-8 plus the earlier attempts:
3,255,314 tokens. No run has yet reached delivery, integration and the external oracle.
