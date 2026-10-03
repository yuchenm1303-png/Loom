# Turn completion

`finish_reason=stop` means a provider response ended. It does not prove that the
user's requested work is complete. Native tool calls continue through the normal
tool loop. Text-only responses are terminal candidates, not final messages.

The canonical `TurnRunner` runs a private, read-only Stop hook before committing
a candidate. The hook receives the frozen generation context, candidate, original
current-turn user inputs recovered from durable events (including steering), and
the cross-turn goal if present. It assesses the requested work against execution
evidence and returns exactly one schema-validated `loom_turn_stop_decision` call.
This call is never dispatched to the tool orchestrator.

The decision distinguishes:

- `completed`: no required work remains, with evidence and a complete answer.
- `continue`: outstanding work and the next authorized action are returned to
  generation in the same logical turn. The candidate is not committed as final.
- `needs_input`: the candidate asks for information/decisions essential to proceed.
- `blocked`: evidence supports a real blocker and the candidate reports the
  incomplete work. A failed tool alone does not establish a blocker.

For the last two outcomes, the conversation turn ends but the task is explicitly
incomplete. `TURN_COMPLETED.stop_decision` records that distinction; it never
automatically marks a cross-turn goal complete. Protocol validity is checked in
code. Semantic accuracy still depends on the reviewing model: this is not a
mathematical guarantee that arbitrary real-world work succeeded.

`TURN_STOP_REQUESTED` and `TURN_STOP_CHECKED` are durable audit events. Review
usage is included in session/goal accounting and review samples count towards
the model-step limit. Review streams are private at both provider and runtime
boundaries. Cancellation and steering use the normal bounded model executor and
the final acceptance lock still rejects superseded samples.

Malformed decisions and assessment errors have bounded retries; exhaustion fails
the turn instead of silently accepting completion. Repeated `continue` decisions
without action also exhaust the normal sampling recovery budget. Nonretryable
duration timeouts are not retried. Existing transport/tool safety checks remain
separate. Waiting-phrase and action-promise regexes are removed from completion
validation; structural/provider truncation checks remain.

Each candidate requires an additional model request, increasing latency and token
usage. Tests for other runtime subsystems stub the Stop service explicitly; the
`real_stop_hook` suite exercises its actual requests and decisions without that
stub. No live-provider acceptance test is implied by scripted regression tests.
