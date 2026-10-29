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
