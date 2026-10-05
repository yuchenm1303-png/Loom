# Turn completion lifecycle

Normal execution no longer runs a mandatory secondary completion model. A valid
terminal actor response ends the turn after tool execution, pending user input,
steering, approvals, and response validation have been handled by the existing
execution loop. A completed turn means the response was delivered; it does not
assert that every task or test passed. Task plans, actual tool results, and the
actor answer retain their own meaning. There is no keyword-based completion
classifier or fixed count of rejected completion assessments.

## Optional checks

Hosts may explicitly pass `stop_hook=review_stop` to AgentRuntime or
ContextAgentRuntime. Production constructors leave this unset. The callback
returns a structured StopDecision; `continue` feeds durable remaining work
back into execution. Cancellation and user steering retain their existing
control flow. Disabling the hook also disables projection of its old
continuation feedback into model requests.

A checker timeout, context-budget failure, or invalid result establishes no
completion verdict. The runtime preserves the actor answer, records
TURN_STOP_CHECKED with assessment_failed and answer_preserved, and records
completion_check=unavailable with no stop_decision on TURN_COMPLETED. It does
not label the check successful, fail the task, or consume another actor retry.
Actual actor/provider failures and explicit host execution limits still use
their existing error paths. This is isolation of an optional check, not a
promise that genuine execution failures cannot occur.

## Source comparison and regression coverage

Compared with public OpenAI Codex revision
a7660cd15490875b8c22f66e577da115ed927fe3, codex-rs/core/src/codex/session/turn.rs:
the default loop follows execution and pending input; Stop hooks are configured
extensions. Loom uses its own provider finish signals and is not an exact copy
of Codex transport. Earlier mandatory-review assumptions are superseded here.

Tests cover long-history follow-up without an extra model call, optional check
failures without fabricated success, malformed callback results, explicit
semantic continuation, steering, cancellation, context rollover, and durable
browser action evidence. Ordinary runtime tests no longer replace the stop
reviewer with an automatically successful fixture.
