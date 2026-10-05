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

## First implementation slice

Native phase and optional end_turn now survive adapters, stream aggregation,
canonical response storage and Responses replay. Tool wrappers preserve the whole
response. MODEL_RESPONSE phase is native or null; display_phase is an explicitly
separate compatibility label consumed by the app server. An inferred display
label never chooses a transition. A pure execution decision handles tool work,
native continuation, pending input and legacy delivery; the existing locked
steering/cancellation/approval handlers still own their lifecycle side effects.
TURN_COMPLETED records its execution end source and task_completion=not_assessed.

No-tool end_turn=false samples again, including an empty native continuation.
Unknown legacy stop behavior is preserved. In particular this slice does not
claim to fix MiniMax's Op 27 stop when it supplies no continuation signal.
No forced control tool or hidden secondary reviewer has been enabled.

Removed code-fence and trailing-bracket truncation inference. Explicit incomplete
finish signals, actual serialized tool protocol and schema validation retain
recovery. Deleted the obsolete prose-shape recovery branch. Plans already carry
recent execution state, so the separate progress projection is omitted when a
plan exists. Native phase is persisted for replay; older stored messages default
to unknown. This remains a staged migration, not a completed harness rewrite.

Default prompt versions have also moved out of runtime.py into explicit snapshots
in system_prompts.py. Migration recognizes exact historical defaults, preserving
custom prompts; older prompt data is no longer built by replacing pieces of the
current prompt. The active default text/version is unchanged in this slice.
Messages tool_use is treated as a completed tool request, not task completion.

## Tool dispatch errors are execution observations

The 0.1.21 browser acceptance run exposed a remaining duplicate validation layer:
TurnRunner rejected native calls before tool dispatch, discarded the entire batch,
then spent provider retries on unavailable tools or invalid arguments. Three
consecutive requests for an unavailable browser evaluation capability terminated
the turn, although the model response protocol itself was valid.

Availability and schema validation now belong only to ToolOrchestrator.prepare.
The existing pending-call loop commits each native call and emits its correlated
TOOL_FAILED result when preparation fails. Such a result explicitly records
execution_status=not_executed and the concrete validation error; it is not a
model-response rejection and does not consume provider retry allowance. Valid
calls in the same batch execute once and retain their own results. Permissions,
approval, credential refusal, cancellation and binding checks still apply.
Malformed/incomplete provider responses retain their separate recovery path.

This matches the public Codex separation between tool dispatch observations
(FunctionCallError::RespondToModel in tools/registry.rs) and provider errors.
It does not guarantee a model will stop requesting an unavailable capability:
the model must use the returned evidence to change approach or report a blocker.
Regression coverage includes recovery after four consecutive tool errors with
zero provider retries, mixed valid/invalid calls without replayed side effects,
and preservation of credential refusal and approval boundaries. Real-model
completion, progress quality and long-history behavior remain separate gates.

## Follow-up chain audit: output ownership and remaining risks

The chain is not yet clean enough to claim full reliability. This audit separates
transport/execution controls from assumptions about model intent. Public Codex
reference: checkout a7660cd15490875b8c22f66e577da115ed927fe3,
codex-rs/core/src/session/turn.rs (needs_followup, explicit end_turn=false,
optional configured Stop hooks) and tools/registry.rs (RespondToModel).

Removed two output/history rewriting layers:

- The compaction-echo filter used three matching lines and an 80-character
  threshold to delete the answer from its first match through the end. Reusing
  checkpoint facts could therefore erase an unrelated, valid final result.
  Checkpoint repetition now leaves the model response intact.
- Tool-only responses acquired runtime-written assistant prose initially and
  every eight tools. This fabricated narration entered both events and model
  history. Native tool calls now retain empty assistant text; tool events remain
  the source of execution status. The unused generator and its tests are removed.

An explicit empty end_turn=false response now reaches the existing SAMPLE
transition instead of the generic empty-response exception. Regression tests
exercise the actual runner, including a following final response.

| Layer | Assessment / next work |
| --- | --- |
| Dispatch availability/schema, permission, approval, credential and binding checks | Necessary execution boundaries; errors must remain correlated tool observations. |
| Provider timeout, cancellation, explicit output truncation | Necessary resource/transport controls; never evidence that the task is complete. |
| Native end_turn and pending tools/input | Own execution transitions; presentation labels do not choose termination. Legacy stop still cannot establish semantic task completion. |
| Text matching serialized tool markup | Remaining heuristic: quoted documentation/code can match. Move provider-specific protocol decoding into adapters with fixtures before removing generic recovery. |
| Inline think-block stripping | Provider compatibility in a generic layer; scope reasoning decoding to the provider contract to avoid deleting quoted public examples. |
| Context budget for unknown model windows | Unknown remains unbounded until provider evidence, while transient guidance still uses a fallback sanity ceiling. Establish one authoritative budget contract and accurate model metadata. |
| Summary language/markup validation | Language and text-shape checks can reject otherwise usable summaries. Separate required structural validity from presentation preference. |
| Repetition/progress guidance | Advisory injected context remains; identical arguments do not prove external-state stagnation. Counts cannot establish task success. |
| Optional Stop review | Still available as opt-in, not enabled by default assembly. Review verdict cannot replace native execution state or authoritative task evidence. |
| Plan and UI progress | Updates reflect explicit plan events; tool count does not establish milestone completion. Model plan accuracy requires real-run evaluation. |
| Sticker decoration | Additional rule-driven text rewriting remains in sticker_body_runtime/streaming_runtime. Presentation annotations should be isolated from canonical execution history and evidence; this audit does not redesign that product feature. |

TurnRunner still combines request recovery, rollover, steering, tool dispatch and
optional review. These removals reduce behavioral rewriting but do not finish
that separation. Future changes need provider fixtures and durable replay tests,
then the same real long-running task with evidence accuracy, plan updates,
progress volume and completion measured independently. Unit passes must not be
reported as successful real-model task completion or as an installed Host update.

Validation of this slice: 159 tests passed across runner completion, dispatch
alignment, bootstrap, execution decisions, durable steering, approval/cancellation,
context rollover and budget semantics, and provider streaming. No live Host was
replaced and no real-provider acceptance run was performed for this slice.
