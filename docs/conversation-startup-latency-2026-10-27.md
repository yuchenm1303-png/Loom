# Conversation startup latency

## Observed failure

The recorded turn asking whether the server was unreachable spent 37,625 ms
before its main generation request. Request preparation accounted for 36,433 ms,
including 29,410 ms in a separate model task summarizing 382 historical messages.
The main request then took 6,765 ms to its first generated content. Its reasoning
setting was already `low`. This is evidence of foreground preparation latency,
not evidence that every delay was model reasoning or a broken connection.

Another turn had a 36-second interval between user input and verification state
initialization. The previous startup path synchronously scanned Git and evaluated
historical acceptance checks. The interval was not finely instrumented enough
to attribute all 36 seconds to one Git command.

## Runtime changes

- Verification initialization carries prior evidence without scanning Git or
  re-evaluating checks. Mutation attribution still captures before/after state
  inside the existing workspace execution lease. Ordinary replies retain access
  to prior obligations without acquiring that mutation lease.
- The newest verification state is read from the log tail. Selective context
  queries now use the append/truncate-safe header index before decoding bodies.
  Unselected tool output and earlier verification payloads are not materialized.
- Modest first requests may defer an explicitly configured soft working-set
  threshold. Hard provider limits, provider overflow recovery, explicit rollover,
  model-native automatic thresholds and large contexts still compact normally.
  This does not introduce a universal working-set cap or truncate tool evidence.
- After a completed long turn, idle preparation may generate one tool-free
  summary. It starts near 75% of the declared threshold, requires credible
  provider usage, has no job queue, skips other active sessions, and has a
  per-session 60-second cooldown. `LOOM_IDLE_COMPACTION=0` disables speculation.
- Idle model work has a separate cancellation token, a 20-second initial-output
  deadline and a 45-second maximum duration. Starting a foreground turn cancels
  it without joining it or waiting for a session lease.
- Workers use deep-copied history and cannot save a session snapshot. Only the
  foreground owner may install a ready candidate, after checking history prefix,
  configuration, platform, language and meaningful size reduction. New input is
  appended verbatim, and exact history is archived through the existing durable
  checkpoint transaction. Budget rejection falls back to ordinary preparation.
- Known model profiles can persist a candidate in `idle-context-summary.json`
  inside the session directory. Atomic replacement and a complete history digest
  permit reuse after restart; edited tool output or configuration invalidates it.
  Custom platforms without inspectable configuration use memory-only candidates.
- Background usage receipts are appended independently of session snapshots,
  including rejected summaries. The foreground owner atomically reconciles each
  receipt once, even after restart; applying a candidate does not charge it again.
- Preparing, summarizing history, waiting for the model, reconnecting and actual
  model activity have distinct runtime notifications and bilingual UI labels.
  Progress-strip dwell timing still avoids rapid label flashes.

## Validation

Regression coverage includes no-scan ordinary questions, selective decoding,
append/rewrite invalidation, tool mutation attribution, candidate cancellation,
model/history/language changes, persistence across restart, edited tool evidence,
exactly-once accounting, soft-limit deferral, mandatory overflow recovery,
streaming identity, steering, durable recovery and UI phase notifications.

`scripts/benchmark-startup.py` compares the old whole-log projection with the
indexed query and asserts equal selected events. With a temporary copy of the
observed approximately 10.9 MB log, three-run median timings were:

| Query | Median |
| --- | ---: |
| Previous whole-log projection | 139.63 ms |
| Indexed selection, cold | 9.19 ms |
| Indexed selection, warm | 1.18 ms |

These numbers measure state lookup only, not a full answer or upstream latency.
The source log is never modified and conversation text is never printed.

Run a synthetic benchmark with `python scripts/benchmark-startup.py`, or pass
`--source-log PATH` to measure a temporary copy of an existing log. The tests use
controlled providers; no live model latency improvement is asserted by them.

The focused runtime regression run passed 243 tests. Follow-up tests also passed
after tightening incomplete-summary rejection and empty-turn selection. Frontend
phase-label tests, TypeScript checking and the production renderer build passed
in a snapshot of committed source overlaid with this change. This isolates
unrelated, concurrently edited lens-rendering files in the shared workspace.

An already running Host needs to load the new Python code, and the renderer
needs a build containing the new UI. Existing active tasks should be allowed to
finish before restarting it. Background summaries reduce avoidable preparation;
they cannot eliminate provider queueing, network delays or mandatory compaction
when no valid candidate is ready.
