# Browser recovery accounting

An unresolved browser operation and a refused follow-up request are not two
executions. The bridge fences input while completion is unknown and reports
follow-ups with `execution_status=not_executed`. These receipts must not consume
the repeated-execution failure budget, including a read-only `browser_state`
request attempting to reconcile the original command.

The progress detector evaluates the final event projection rather than returning
at the first historical threshold crossing. A later successful browser operation
or workspace diff clears the preceding sequence. Real repeated unknown execution
failures, repeated unchanged browser observations and repeated invalid tool
requests retain their existing guards.

No bridge input fence is removed. This change neither replays the original
operation nor claims a timed-out command succeeded. The model may observe again,
choose another tool, or explain the unavailable browser. The original timeout
cause remains separate from this accounting defect.

The user-facing limit message explains that recovery made no progress and that
existing work is preserved. Internal operation fingerprints stay in tool receipts
instead of being displayed as the explanation.

`tests/test_browser_recovery_accounting.py` covers the production sequence,
blocked observations, later recovery in one batch, retained loop guards and an
end-to-end turn that observes successfully without repeating the original input.
