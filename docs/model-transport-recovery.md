# Model transport recovery

The recovery clock starts when the first retryable failure is observed, not at
the start of the failed model request. Initial connection latency, valid streamed
generation and the initial stall timeout therefore cannot consume the entire
recovery budget before the first recovery attempt.

The budget measures time without a working provider. A retry that streamed
output before failing restarts the clock: the provider was answering, and the
length of that sample is not waiting. Retries that fail without output still add
up, and `transport_retries` caps the count either way. Without the restart a
second stall (90s) always landed just past the 90s budget, so the stall-recovery
instruction ("split large writes") could never be sent.

Once a stream has produced output (reasoning deltas count), a gap of
`MID_STREAM_STALL_SECONDS` (90s) ends it, instead of the 300s idle default; a
profile that set its own `stream_idle_timeout_seconds` keeps it. Measured over
~5,600 successful responses on the development machine, one gap after first
output exceeded 90s (148s, MiniMax-M3). Before the first output the 300s
first-output deadline is unchanged. Session 40b9eb81 (2026-10-28) waited 300s on
a stream that died after 1,647 chunks and then failed the turn.

Subsequent recovery attempts and backoff remain inside the configured recovery
window and retry count. Retry-After exceeding that window does not cause a long
unbounded sleep. Permanent errors and maximum-duration failures remain terminal.
Stop and steering interrupt backoff through the existing signal-aware wait.

A timeout or transport failure after generated content allocates a new sampling
step before retrying. Abandoned deltas cannot append to the replacement sample;
only a complete successful response can be committed. Completed tools remain in
canonical history and are not replayed by this recovery path.

Regression coverage: `tests/test_recovery_budget_clock.py` simulates a 340-second
first failure without sleeping, checks first-output/stall/transport recovery,
fresh sample identity, and exhaustion after subsequent slow failures. Existing
alignment and deadline tests cover completed-tool preservation, cancellation,
permanent errors, healthy streams and abandoned request isolation.

This fixes local recovery semantics; it does not establish why an upstream or
network stopped delivering bytes. Persistent failures still surface after the
configured budget is exhausted.
