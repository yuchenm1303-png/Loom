# Turn completion

`finish_reason=stop` means a provider response ended. It does not prove that the
user's requested work is complete. Native tool calls continue through the normal
tool loop. Text-only responses are terminal candidates, not final messages.

The canonical `TurnRunner` runs a private, read-only Stop hook before committing
a candidate. The hook receives fresh assessment context: candidate, actual user
inputs recovered from durable events (including steering), milestone state,
attributed execution excerpts, relevant prior results and the cross-turn goal if
present. Actor chatter and retry instructions are excluded. It assesses requested work against execution
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
the turn instead of silently accepting completion. Valid `continue` decisions
resume normal execution in the same turn. They have no rejection-count limit,
including across tool batches and approval resumptions. Completion requires an
accepted semantic result; cancellation, actual failures and explicitly configured
resource budgets remain independent. See [task convergence](task-convergence.md). Nonretryable
duration timeouts are not retried. Existing transport/tool safety checks remain
separate. Waiting-phrase and action-promise regexes are removed from completion
validation; structural/provider truncation checks remain.

Each candidate requires an additional model request, increasing latency and token
usage. Tests for other runtime subsystems stub the Stop service explicitly; the
`real_stop_hook` suite exercises its actual requests and decisions without that
stub. No live-provider acceptance test is implied by scripted regression tests.

## Public Codex comparison

The audited public source is `openai/codex@b741e480e203f037ca726bc2a76d99a8e8668e66`,
also checked against local source `a7660cd15490875b8c22f66e577da115ed927fe3`:

- [Turn loop](https://github.com/openai/codex/blob/b741e480e203f037ca726bc2a76d99a8e8668e66/codex-rs/core/src/session/turn.rs)
  combines model follow-up and pending input. Only when neither remains does it
  run Stop hooks; a blocking hook with continuation context returns to the loop.
- [Hook runtime](https://github.com/openai/codex/blob/b741e480e203f037ca726bc2a76d99a8e8668e66/codex-rs/core/src/hook_runtime.rs)
  passes `stop_hook_active` as boolean history metadata. It is not a retry counter
  and does not impose Loom's former three-rejection limit.
- [Official hook documentation](https://learn.chatgpt.com/docs/hooks#stop)
  defines `decision: "block"` as a request to continue, with a reason supplied as
  continuation context. Configured hooks can also explicitly request a stop.

Codex's public flow does not establish that every task receives an independent
model completion judge. Loom's read-only semantic assessor is an additional
service. It must not be described as identical to Codex or as a guarantee of
real-world completion. The removed three-rejection limit was a Loom design
mistake, not an upstream parity requirement.
