# Turn completion lifecycle

A turn ends when the provider gives no native tool calls and does not explicitly
request another sample (`end_turn=False`), after pending user input, steering,
approval, cancellation and response validation are handled. Ordinary tool calls
continue the same turn. This is a response-delivery protocol, not proof that the
user task or every test has passed.

There is no hidden completion model, optional `stop_hook` constructor argument,
Stop assessment context budget, rejection-count policy, or natural-language
completion classifier. Plans and checks retain explicit outcomes and references
to executed evidence. They do not override provider continuation signals or
silently invent user requirements. `task_completion=not_assessed` describes the
turn protocol's limited claim; the actor reports actual results.

`_emit_event` routes explicit response/terminal lifecycle hooks. `_record` appends
an event without lifecycle side effects. Tests simulating production events must
use the former; event inspection can use the latter.

The fixed comparison source is
[Codex session/turn.rs:566](https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/session/turn.rs#L566):
model follow-up and pending input determine continued execution. Codex also has
configured hooks; Loom does not implement its previous semantic reviewer as such
a parity requirement.

Regression coverage retains explicit provider continuation, empty native
continuation, long-history delivery without a reviewer, same-turn steering,
cancellation, approvals, real context rollover, durable plans and browser action
evidence. A constructor/source guard rejects reintroducing the removed reviewer.
