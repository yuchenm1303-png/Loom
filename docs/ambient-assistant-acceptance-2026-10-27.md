# Ambient assistant — installed Windows acceptance, 2026-10-27

Tested the user's installed Loom **0.1.133** through its existing authenticated local App Server. The live Host advertised ambient protocol v1 and all four trigger kinds. Tests created isolated workspaces and six test sessions, used the configured model, and retained real thread/turn/usage records. No external messages were sent, no approvals were accepted, and no permission settings were changed. These were real model calls, not the design-preview fixture.

## Observed results

| Case | Observation | Result |
| --- | --- | --- |
| One-shot timer | One logical run completed with `AMBIENT_ONCE_OK`; no periodic repetition. | Pass |
| Idle file watcher | Six seconds with no file change produced no run. | Pass for the observed interval |
| File change | Changing the isolated `watch.txt` produced one file-source run; the model read and returned `FILE_EVENT_20261027_1`. | Pass |
| Event deduplication | Five deliveries with keys `1,1,1,2,3` returned acceptance `true,false,false,true,true`. | Pass |
| Coalescing and busy admission | During a manual turn on the same test session, the automatic run remained `queued` with `merged=3`, while the thread reported `running` and `active=true`. After the manual turn, exactly one automatic run completed with `AMBIENT_EVENT_OK`. | Pass |
| Global pause | A due interval task produced no run during seven seconds of pause. The original unpaused state was restored. | Pass |
| Stop and disable | Cancelling the admitted interval run disabled its trigger. Thirteen more seconds (over three four-second intervals) produced no second run. | Pass, with diagnostic issue below |
| Admission limit | Re-enabling the already-used file task and changing its file again produced a `limited` record; `runsUsed` stayed 1 and the task disabled itself. No second file-processing turn started. | Pass |
| Desktop window close | Closed the actual Loom Desktop with Alt+F4. App inventory then contained no Loom window; the Desktop process exited, while the same Host PID 24616 and runtime PID 25180 remained. A newly armed one-shot subsequently completed with `AMBIENT_CLOSED_UI_OK`, recording 15,748 tokens. Desktop was then reopened. | Pass |
| Approval waiting | An approval-mode test requested `exec_command` to write only an isolated sentinel file. It entered `waiting_approval`; the file did not exist before approval. A subsequent event did not start another turn (it reached the configured one-run limit). Cancelling the task cancelled the pending approval; the file still did not exist. | Pass; no approval granted |

The first attempt to test closing Desktop was interrupted by an accidental physical Escape. Its already-scheduled one-shot ran with Desktop open and **is not** evidence for window independence. The task was explicitly rearmed with a two-run limit; only its second run is counted as the successful closed-window test above.

## Diagnostic repair found by the live test

Cancelling during App Server preparation worked, but the installed scheduler attached `Thread changed before result reconciliation; inspect thread` to a cancelled run. Preparation cancellation returns before a runtime turn id is written, so comparing only the session's current turn id incorrectly treated the absent turn as an unknown outcome.

The repair persists the existing App Server cancellation acknowledgment's `requested`, `turnId`, and `status` fields in the run. Only an acknowledged interruption of that exact preparation, with no recorded preparation failure, reconciles as a clean zero-token cancellation. Missing or mismatched acknowledgments still require review. A genuinely unknown cancellation outcome is no longer relabeled as a known cancellation. Nothing resumes or approves a pending operation.

This repair is source-tested, **not yet installed into the running 0.1.133 Host**. Updated preparation-cancellation assertions and an uncertain-outcome regression passed with the related suite:

```text
pytest tests/test_ambient.py tests/test_ambient_service.py \
  tests/test_loom_host_background_runtime.py tests/test_event_index.py \
  tests/test_app_server.py tests/test_app_server_local_ipc.py -ra
54 passed in 27.28s
```

## Cleanup and limits of this acceptance

All six test tasks were disabled and the six test sessions archived after their work stopped; run/result/usage evidence remains available. The scheduler was left online and unpaused, as initially found. Isolated test artifacts and machine-readable results are retained outside Git under the main workspace's `.cache/ambient-live-2026-10-27/`; they contain test workspaces and results, not the IPC authentication token. Recorded automatic task usage was **106,544 tokens**; this is the scheduler's usage delta, not a billing calculation, and excludes the separate manual turn used to hold the queue busy.

**Actual Host stop/start and machine sleep/sign-out were not performed.** Another user session remained active throughout the acceptance, so restarting the shared Host would interrupt unrelated work. Same-Host Desktop close/reopen passed; it must not be confused with Host restart. Restart deduplication/review and approval-boundary behavior remain covered by the automated suite, with a live installed Host restart still awaiting an idle opportunity. The idle test establishes no model turn for an unchanged watched file during the observation; it does not claim a long-duration soak or zero total token usage across unrelated user sessions.
