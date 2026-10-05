# Agent runtime architecture review and refactor plan

Status: research baseline; protocol changes require replay and provider validation
before production activation. No new completion classifier is authorized by this
review. Public Codex source is not the private desktop implementation.

## Evidence and corrected assumptions

Audited public OpenAI Codex commit a7660cd15490875b8c22f66e577da115ed927fe3.
Actual path is codex-rs/core/src/session/turn.rs (earlier documentation incorrectly
included codex/session). Around lines 554-565 the loop combines model follow-up
with pending input. Around 640 onward configured Stop hooks can block and supply
continuation input. Around 2994 an explicit end_turn=false requests follow-up.
This is a provider control signal, not inference from answer prose. Do not claim
that phase alone controls turn completion or that Codex guarantees task success.

Loom turn_runner.py currently synthesizes phase=commentary when calls exist and
phase=final_answer otherwise. ModelResponse has finish_reason but no native phase
or end_turn field. Thus historical log phase is presentation inference, not a
provider signal. The stress run ending at Op 27 contains stop plus no tool call;
it does not contain an upstream final_answer control field. That distinction
corrects earlier diagnoses. Its unfinished plan is also not an independent proof
that the user intended unlimited continuation.

Official references:
- https://learn.chatgpt.com/docs/app-server : thread/turn/item lifecycle, optional
  agentMessage phase and plan events.
- https://developers.openai.com/api/docs/guides/compaction : compaction preserves
  provider state through opaque items; standalone output is used as-is. Such
  provider-specific items cannot be assumed transferable between arbitrary models.
- https://github.com/openai/codex/blob/a7660cd15490875b8c22f66e577da115ed927fe3/codex-rs/core/src/session/turn.rs

## Responsibilities

Provider adapter: normalize typed stream outcomes, preserve native phase and
end-turn intent with provenance, report unknown when absent. Finish reason records
transport completion only. Adapter owns provider format quirks and capabilities;
it must not inspect natural-language phrases to determine intent.

Execution controller: deterministic reducer for model events, pending tool calls,
completed tool receipts, steering, approval, cancellation and genuine errors.
States: sampling, executing, waiting_for_approval, waiting_for_user, ended,
interrupted, failed. Decisions: execute tools, sample again, wait, deliver answer.
Pending work/input is drained before end. Native continue yields another sample;
unknown legacy intent retains documented compatibility behavior, not fabricated
certainty. Transition priority: cancellation, accepted steering, pending tools,
explicit continuation, explicit end, legacy completion. Existing approval semantics
must remain intact.

Task state: goal, scope, milestones, evidence references and unresolved work. Task
completion is distinct from turn delivery. Plans are assistant assertions with
supporting references, not an automatic completion oracle. No tool-count-based
progress or forced completion because text says 'continue'.

Context service: durable canonical messages/results and reconstructable task state
are separate from the model window. One context composer supplies current task
state once. Summary remains a lossy handoff; preserve corrections and uncertainty.
Changing providers rebuilds a compatible window using durable records; opaque
provider reasoning/compaction items stay provider-bound. Exact action results stay
retrievable. Never replay state-changing actions to rebuild context.

Presentation: display provider phase separately from inferred legacy display phase.
The UI consumes item/turn/task events and does not choose execution transitions.
Progress prose is not task state. No silent censorship to simulate efficiency.

## Alternatives and limits

Reject keyword classifiers and mandatory full-history secondary completion models.
Also reject gating every final answer on all plan items being completed: legitimate
status replies, blockers and user scope changes must still be deliverable.

For providers lacking native continuation intent, investigate an explicit typed
control tool (continue/deliver/wait) only as a capability-specific protocol. It is
not an extra reviewer, and must be validated with the actual provider's tool-choice
support before adoption. Forcing every model into an untested envelope is not an
established optimal solution. An explicit deliver decision remains a model claim,
not proof of external success. No architecture guarantees perfect model judgment.

## Migration sequence and acceptance gates

1. Add lossless adapter fields and intent provenance, initially observational.
   Capture missing/contradictory native fields and retain legacy compatibility.
2. Extract a pure execution transition reducer from turn_runner, while replaying
   existing behavior. Compare traces for tool/approval/steering/cancel paths.
3. Centralize task/context composition; consolidate duplicate guidance in runtime,
   execution_guidance, execution_progress_context and plan_context. Replace default
   prompt string replacement construction with explicit versioned migration data.
4. Remove prose-shape completion guesses (dangling bracket/code-fence rejection)
   after tests separate valid Markdown from genuine incomplete transport. Preserve
   structured tool schema validation and explicit provider truncation recovery.
5. Evaluate capability-specific control with MiniMax and native providers. Do not
   turn it on globally based on scripted tests alone. Optional hooks remain separate
   extensions; hook failure must never invent task success or erase a valid answer.
6. Roll out only after replay and provider evaluation. Preserve rollback compatibility
   and existing sessions. Record deployed host version; main commits do not deploy it.

Required trace cases: reported Op 27 premature stop; plain status question with an
unfinished plan; native end_turn=false with no tools; phase absent; completed tool
batch with pending steering; cancellation during sampling/compaction; approval
resume; schema failure vs valid text ending in '['; provider length truncation;
long-history corrections surviving rollover; provider switch with incompatible
opaque context; restart with pending state-changing tool (no duplicate action).

Metrics distinguish behavior: premature task interruption, actual completed
milestones and evidence accuracy, unnecessary sampling/review calls, repeated
mutations, progress-message volume, input tokens and latency. Passing unit tests
establishes transitions, not model efficacy. Real provider evaluation is a gate,
not an optional claim to add after release.
