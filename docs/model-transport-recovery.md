# Model transport recovery

The recovery clock starts when the first retryable failure is observed, not at
the start of the failed model request. Initial connection latency, valid streamed
generation and the initial stall timeout therefore cannot consume the entire
recovery budget before the first recovery attempt.

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
