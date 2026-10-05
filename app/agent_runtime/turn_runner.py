"""The single model/tool state machine used by both core and extended runtimes."""
from __future__ import annotations

import json
import time
from dataclasses import replace

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIEmptyResponseError, AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled, ModelSteered

from .context_budget import is_context_window_error, request_forced_compaction
from .contracts import AgentEventKind as Event
from .contracts import AgentStatus
from .execution_binding import action_binding_digest
from .history import repair_tool_history
from .model_replan import revision as steering_revision
from .model_replan import wait_for_signal
from .model_execution import ModelRequestTimeout
from .execution_state import ExecutionAction, next_execution_action
from .tools import BLOCKED_SENSITIVE_INPUT_ARGUMENT, validate_tool_arguments
from .turn_response_validation import (
    COMPLETE_FINISH_REASONS,
    TERMINAL_RECOVERY_INSTRUCTION,
    TRUNCATED_RECOVERY_INSTRUCTION,
    TOOL_ARGUMENT_RECOVERY_INSTRUCTION,
    history_message_count,
    invalid_terminal_response,
    merge_recovery_text,
    strip_compaction_echo,
)


# Compatibility aliases retained for focused tests and callers that imported the
# previous module-private validation helpers.
_COMPLETE_FINISH_REASONS = COMPLETE_FINISH_REASONS
_TERMINAL_RECOVERY_INSTRUCTION = TERMINAL_RECOVERY_INSTRUCTION
_TRUNCATED_RECOVERY_INSTRUCTION = TRUNCATED_RECOVERY_INSTRUCTION
_invalid_terminal_response = invalid_terminal_response
_strip_compaction_echo = strip_compaction_echo
_history_message_count = history_message_count


def _exposed_tool_names(step) -> tuple[str, ...]:
    return tuple(sorted(tool.name for tool in step.tool_router.all()))


def _sample_model_identity(step) -> dict[str, str]:
    """Only persist attribution fields from the immutable request snapshot."""
    profile = json.loads(step.request_state.model_profile_json or "{}")
    return {
        "model": str(profile.get("model") or ""),
        "provider": str(profile.get("provider") or ""),
        "profile_id": step.world_state.profile_id,
    }


def _consume_steering_for_sample(rt, session, token) -> int:
    """Consume all guidance known before a model request and return its revision.

    A steering submission can race the inbox read. Re-read until the process-local
    revision is stable; if guidance arrives immediately after this returns, the
    executor receives the older revision and supersedes the request before its
    result can commit.
    """

    while True:
        expected = steering_revision(token)
        rt._consume_steering(session)
        if steering_revision(token) == expected:
            return expected


def _record_steering_rejection(
    rt,
    session,
    step,
    *,
    attempt: int,
    usage: ModelUsage | None = None,
    response: ModelResponse | None = None,
) -> None:
    resolved_usage = usage or ModelUsage()
    data = {
        **_sample_model_identity(step),
        "step_id": step.step_id,
        "reason": "superseded_by_steering",
        "attempt": attempt,
        "usage": {
            "input_tokens": resolved_usage.input_tokens,
            "output_tokens": resolved_usage.output_tokens,
            "total_tokens": resolved_usage.total_tokens,
        },
    }
    if response is not None:
        data.update({
            "finish_reason": response.finish_reason,
            "response_id": response.response_id,
        })
    rt._record(session, Event.MODEL_RESPONSE_REJECTED, data=data)


class TurnRunner:
    def __init__(self, runtime):
        self.runtime = runtime

    def run(self, session, token):
        rt = self.runtime
        try:
            while True:
                if rt._cancel_if_requested(session, token):
                    return rt._result(session)
                if session.pending_tool_calls and not rt._process_pending_tools(session, token):
                    return rt._result(session)
                rt._consume_steering(session)
                if rt.limits.max_model_steps > 0 and session.model_steps >= rt.limits.max_model_steps:
                    return rt._limit(session, "model step limit reached")

                recovery_instruction = ""
                recovery_tool_hint = ""
                recovery_partial = ""
                recovery_reasoning = ""
                stop_decision = None
                attempt = 0
                while True:
                    if rt._cancel_if_requested(session, token):
                        return rt._result(session)
                    if rt.limits.max_model_steps > 0 and session.model_steps >= rt.limits.max_model_steps:
                        return rt._limit(session, "model step limit reached")
                    sample_steering_revision = _consume_steering_for_sample(rt, session, token)
                    request_preparation_started = time.perf_counter()
                    # Capture once so request context, advertised tools, and all
                    # tool calls from this response share one immutable world.
                    step = rt._capture_step_context(session, next_model_step=True)
                    messages, extra = rt._prepare_model_request(session, step, token)
                    if (
                        rt.limits.max_messages > 0
                        and _history_message_count(messages) > rt.limits.max_messages
                    ):
                        return rt._limit(
                            session,
                            "context message limit reached; no safe compaction boundary",
                        )
                    reasoning = step.reasoning
                    profile_id = step.world_state.profile_id
                    # Attribute this sample to its frozen request configuration,
                    # not a session model that may change before it completes.
                    model_identity = _sample_model_identity(step)
                    tool_names = _exposed_tool_names(step)
                    request_messages = list(messages)
                    if recovery_instruction and recovery_instruction != "stop_check_continue":
                        if recovery_partial:
                            request_messages.append(AIMessage(
                                role=MessageRole.ASSISTANT,
                                content=recovery_partial,
                                reasoning=recovery_reasoning,
                            ))
                        request_messages.append(AIMessage(
                            role=MessageRole.SYSTEM,
                            name="loom_terminal_recovery",
                            content=(
                                TOOL_ARGUMENT_RECOVERY_INSTRUCTION + recovery_tool_hint
                                if recovery_instruction == "invalid_tool_arguments"
                                else
                                _TRUNCATED_RECOVERY_INSTRUCTION
                                if recovery_partial
                                else _TERMINAL_RECOVERY_INSTRUCTION
                            ),
                        ))
                    context_limits = extra.get("context_limits") if isinstance(extra, dict) else None
                    if not isinstance(context_limits, dict):
                        context_limits = {}
                    # Only impose an output cap that something authoritative asked
                    # for. Loom's own reserve is input-budget bookkeeping; sending
                    # it as max_tokens truncates a reasoning model mid-answer,
                    # because its chain of thought spends the same budget.
                    resolved_output_reserve = (
                        int(context_limits.get("output_reserve_tokens") or 0)
                        if context_limits.get("output_reserve_declared")
                        else None
                    )
                    request = ChatRequest(
                        messages=tuple(request_messages),
                        tools=step.tool_router.definitions(),
                        tool_choice=ToolChoice.AUTO,
                        max_output_tokens=resolved_output_reserve or None,
                        reasoning=reasoning,
                        session_id=session.session_id,
                        parallel_tool_calls=True,
                    )
                    request_preparation_ms = round(
                        (time.perf_counter() - request_preparation_started) * 1000
                    )

                    # Transport retries are retries of this exact request, not a
                    # new semantic sampling step. Keep both the StepContext and
                    # prepared request stable until the provider yields a response.
                    retry_sampling = False
                    while True:
                        model_request_started = time.perf_counter()
                        rt._record(session, Event.MODEL_REQUESTED, data={
                            "profile_id": profile_id,
                            "step": step.model_step,
                            "step_id": step.step_id,
                            "message_count": len(messages),
                            "tool_count": len(tool_names),
                            "tool_names": list(tool_names),
                            "permission_mode": step.world_state.permission_mode.value,
                            "reasoning": reasoning.as_safe_dict() if reasoning is not None else None,
                            "attempt": attempt,
                            "request_preparation_ms": request_preparation_ms,
                            **extra,
                            **model_identity,
                        })
                        try:
                            response = rt.model_executor.execute(
                                rt.platform_for_session(session.session_id),
                                profile_id,
                                request,
                                token,
                                steering_revision=sample_steering_revision,
                            )
                            model_execution_ms = round((time.perf_counter() - model_request_started) * 1000)
                            break
                        except ModelSteered:
                            # The durable inbox contains the new intent. Discard
                            # only this not-yet-committed model sample, keep the
                            # logical turn alive, and rebuild the next request from
                            # history with the steering message included. Recording
                            # the rejection also closes any transient streamed UI
                            # item for this abandoned sample.
                            _record_steering_rejection(
                                rt,
                                session,
                                step,
                                attempt=attempt,
                            )
                            rt._release_step_context(step)
                            recovery_instruction = ""
                            recovery_partial = ""
                            recovery_reasoning = ""
                            retry_sampling = True
                            break
                        except AIEmptyResponseError as exc:
                            from .runtime import _add_usage

                            session.model_steps += 1
                            rejected_usage = ModelUsage(
                                input_tokens=exc.input_tokens,
                                output_tokens=exc.output_tokens,
                                total_tokens=exc.total_tokens,
                            )
                            session.usage = _add_usage(session.usage, rejected_usage)
                            rt._record(session, Event.MODEL_RESPONSE_REJECTED, data={
                                **model_identity,
                                "step_id": step.step_id,
                                "reason": "reasoning_only_response" if exc.reasoning_char_count else "empty_response",
                                "finish_reason": exc.finish_reason,
                                "response_id": exc.response_id,
                                "reasoning_char_count": exc.reasoning_char_count,
                                "stream_chunk_count": exc.chunk_count,
                                "attempt": attempt,
                                "usage": {
                                    "input_tokens": exc.input_tokens,
                                    "output_tokens": exc.output_tokens,
                                    "total_tokens": exc.total_tokens,
                                },
                            })
                            rt._release_step_context(step)
                            if attempt >= rt.limits.model_retries:
                                raise RuntimeError(
                                    "model repeatedly completed without public text or tool calls"
                                ) from exc
                            attempt += 1
                            recovery_instruction = "empty_response"
                            recovery_partial = ""
                            recovery_reasoning = ""
                            retry_sampling = True
                            break
                        except AIResponseError as exc:
                            # An over-length rejection is not a malformed response:
                            # the request was valid and simply did not fit. Retrying
                            # it verbatim cannot work, and the recovery instruction
                            # below would only make it longer. Compact instead.
                            over_length = is_context_window_error(exc)
                            session.model_steps += 1
                            rt._record(session, Event.MODEL_RESPONSE_REJECTED, data={
                                **model_identity,
                                "step_id": step.step_id,
                                "reason": (
                                    "context_window_exceeded"
                                    if over_length
                                    else "invalid_provider_response"
                                ),
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                                "attempt": attempt,
                                # The size the provider refused. For a model with
                                # no declared window this is the only hard fact
                                # available about it, so record it as a bound
                                # rather than rediscovering it every time.
                                "rejected_input_tokens": (
                                    int(extra.get("calibrated_input_tokens_after") or 0)
                                    if over_length and isinstance(extra, dict)
                                    else 0
                                ),
                                "usage": {
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "total_tokens": 0,
                                },
                            })
                            rt._release_step_context(step)
                            if attempt >= rt.limits.model_retries:
                                if over_length:
                                    raise RuntimeError(
                                        "model context window is smaller than the configured limits "
                                        f"and compaction could not recover: {exc}"
                                    ) from exc
                                raise RuntimeError(
                                    f"model repeatedly returned malformed responses: {exc}"
                                ) from exc
                            attempt += 1
                            if over_length:
                                request_forced_compaction(rt, session)
                                recovery_instruction = ""
                            else:
                                recovery_instruction = "invalid_provider_response"
                            recovery_partial = ""
                            recovery_reasoning = ""
                            retry_sampling = True
                            break
                        except (AITransportError, ModelRequestTimeout) as exc:
                            if isinstance(exc, ModelRequestTimeout):
                                rt._record(session, Event.MODEL_RESPONSE_REJECTED, data={
                                    **model_identity,
                                    "step_id": step.step_id,
                                    "reason": exc.reason,
                                    "error_type": type(exc).__name__,
                                    "error": str(exc),
                                    "attempt": attempt,
                                    "retryable": exc.retryable,
                                    "will_retry": exc.retryable and attempt < rt.limits.model_retries,
                                })
                            if not exc.retryable or attempt >= rt.limits.model_retries:
                                raise
                            next_attempt = attempt + 1
                            signal = wait_for_signal(
                                token,
                                sample_steering_revision,
                                min(2.0, 0.25 * 2 ** (next_attempt - 1)),
                            )
                            if signal == "cancel":
                                raise ModelCancelled()
                            if signal == "steer":
                                _record_steering_rejection(
                                    rt,
                                    session,
                                    step,
                                    attempt=attempt,
                                )
                                rt._release_step_context(step)
                                recovery_instruction = ""
                                recovery_partial = ""
                                recovery_reasoning = ""
                                retry_sampling = True
                                break
                            attempt = next_attempt
                            if isinstance(exc, ModelRequestTimeout):
                                # A timed-out sample may already have streamed a
                                # partial reply. Give the retry a fresh step so
                                # its deltas cannot append to that abandoned text.
                                rt._release_step_context(step)
                                retry_sampling = True
                                break
                            continue

                    if retry_sampling:
                        continue
                    if not isinstance(response, ModelResponse):
                        raise TypeError("agent model platform must return ModelResponse")
                    # A syntactically unfinished response is retried in the same
                    # logical model step with the rejected partial in context. The
                    # retry usually emits only the missing suffix. Reconstruct the
                    # self-contained assistant message before validation/commit;
                    # otherwise Loom would permanently discard the already-shown
                    # prefix and make a successful recovery look truncated.
                    # An action promise is replayed as context, never appended to
                    # the replacement answer: it is progress, not a cut-off prefix.
                    if (
                        recovery_partial and response.text and not response.tool_calls
                    ):
                        merged_text = merge_recovery_text(recovery_partial, response.text)
                        if merged_text != response.text:
                            merged_response = replace(
                                response,
                                text=merged_text,
                                reasoning=f"{recovery_reasoning}{response.reasoning}",
                            )
                            # Providers are free to either continue the supplied
                            # partial or replace it with a fresh self-contained
                            # answer. Prefer the merge when it becomes valid, or
                            # when the retry is itself still incomplete. If the
                            # retry is complete but the merge is not, it clearly
                            # chose the replacement strategy; do not poison it
                            # with the abandoned prefix.
                            merged_invalid = _invalid_terminal_response(merged_response)
                            retry_invalid = _invalid_terminal_response(response)
                            if not merged_invalid or retry_invalid:
                                response = merged_response

                    clean_text, compaction_echo_removed = _strip_compaction_echo(
                        messages,
                        response.text,
                    )
                    if compaction_echo_removed:
                        response = replace(response, text=clean_text)
                    invalid_terminal = (
                        "compaction_echo"
                        if compaction_echo_removed and not response.tool_calls
                        else _invalid_terminal_response(response)
                    )
                    # Validate the entire native-call batch before committing any
                    # assistant/tool history or executing even its valid prefix.
                    # Security refusals still belong to the orchestrator; never
                    # turn a blocked credential input into an argument retry.
                    if not invalid_terminal and response.tool_calls:
                        for call in response.tool_calls:
                            tool = step.tool_router.get(call.name)
                            if tool is None:
                                invalid_terminal = "invalid_tool_arguments"
                                recovery_tool_hint = " The requested tool is unavailable; choose an advertised tool."
                                break
                            if isinstance(call.arguments, dict) and BLOCKED_SENSITIVE_INPUT_ARGUMENT in call.arguments:
                                continue
                            try:
                                validate_tool_arguments(tool.input_schema, call.arguments)
                            except ValueError:
                                invalid_terminal = "invalid_tool_arguments"
                                recovery_tool_hint = (
                                    f" Invalid tool: {tool.name}. Required properties: "
                                    + json.dumps(tool.input_schema.get("required", []))
                                    + ". Consult its schema for property types."
                                )
                                break
                    if (not invalid_terminal and not response.tool_calls and response.end_turn is not False
                            and rt.stop_hook is not None):
                        try:
                            stop_decision = rt.stop_hook(rt, session, step, token, request,
                                                        response, sample_steering_revision)
                            from .turn_stop import StopDecision
                            if not isinstance(stop_decision, StopDecision):
                                raise ValueError("Stop check must return a structured StopDecision")
                        except ModelSteered:
                            from .runtime import _add_usage
                            rt._release_step_context(step)
                            _record_steering_rejection(rt, session, step, attempt=attempt,
                                                      response=response, usage=response.usage)
                            session.usage = _add_usage(session.usage, response.usage)
                            recovery_instruction = ""
                            recovery_partial = ""
                            continue
                        except ModelCancelled:
                            raise
                        except Exception as exc:
                            # An unavailable check establishes no verdict. Keep
                            # the candidate and record the check failure separately.
                            stop_decision = None
                            rt._record(session, Event.TURN_STOP_CHECKED, data={
                                "step_id": step.step_id, "outcome": "assessment_failed",
                                "error_type": type(exc).__name__, "answer_preserved": True,
                            })
                        if stop_decision is not None and stop_decision.outcome == "continue":
                            invalid_terminal = "stop_check_continue"
                            # Durable assessment state is projected on every next
                            # request, including after tools, approvals and compaction.
                    if not invalid_terminal:
                        break

                    from .runtime import _add_usage

                    session.model_steps += 1
                    session.usage = _add_usage(session.usage, response.usage)
                    rt._record(session, Event.MODEL_RESPONSE_REJECTED, data={
                        **model_identity,
                        "step_id": step.step_id,
                        "reason": invalid_terminal,
                        "finish_reason": response.finish_reason,
                        "response_id": response.response_id,
                        "text_preview": str(response.text or "")[-240:],
                        "attempt": attempt,
                        "usage": {
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "total_tokens": response.usage.total_tokens,
                        },
                    })
                    rt._release_step_context(step)
                    # A semantic continuation is normal task execution, not a
                    # failed provider request. Neither successful tool batches
                    # nor repeated candidates decide when the task is done.
                    # Cancellation, steering and explicit resource budgets are
                    # checked by the ordinary execution loop and model executor.
                    if invalid_terminal != "stop_check_continue" and attempt >= rt.limits.model_retries:
                        raise RuntimeError(
                            f"model repeatedly returned an invalid terminal response ({invalid_terminal})"
                        )
                    if invalid_terminal != "stop_check_continue":
                        attempt += 1
                    recovery_instruction = invalid_terminal
                    resume_from_partial = invalid_terminal.startswith("incomplete_finish:")
                    recovery_partial = str(response.text or "") if resume_from_partial else ""
                    # The replayed assistant turn must carry the reasoning that
                    # produced it, or a thinking-mode provider rejects the whole
                    # request rather than continuing from it.
                    recovery_reasoning = response.reasoning if resume_from_partial else ""

                if rt._cancel_if_requested(session, token):
                    return rt._result(session)
                if not isinstance(response, ModelResponse):
                    raise TypeError("agent model platform must return ModelResponse")
                if not response.text and not response.tool_calls:
                    raise RuntimeError("agent model response contained neither text nor tool calls")

                runtime_authored_commentary = False
                if response.tool_calls and not str(response.text or "").strip():
                    # A tool-only response is valid at the provider layer, but accepting
                    # it verbatim leaves the user staring at an unexplained command stream.
                    # Supply a safe, language-aware preamble without reflecting arguments,
                    # which may contain credentials, into the public transcript.
                    from .tool_commentary import (
                        runtime_tool_commentary,
                        should_emit_runtime_tool_commentary,
                    )
                    if should_emit_runtime_tool_commentary(
                        rt.store.events(session.session_id),
                        turn_id=session.current_turn_id,
                    ):
                        response = replace(
                            response,
                            text=runtime_tool_commentary(
                                response.tool_calls,
                                communication_language=session.communication_language,
                                continuing=session.tool_calls > 0,
                            ),
                        )
                        runtime_authored_commentary = True

                # Serialize the final sample-acceptance boundary against steering
                # submission. ModelExecutor already notices guidance during token
                # generation; this closes the final race after the provider has
                # returned but before MODEL_RESPONSE becomes durable. Whichever
                # side obtains this guard first defines the ordering.
                superseded_before_commit = False
                cancelled_before_commit = False
                from .runtime import _add_usage
                with rt._active_tokens_guard:
                    if token.cancelled:
                        cancelled_before_commit = True
                    elif steering_revision(token) != sample_steering_revision:
                        session.usage = _add_usage(session.usage, response.usage)
                        _record_steering_rejection(
                            rt,
                            session,
                            step,
                            attempt=attempt,
                            usage=response.usage,
                            response=response,
                        )
                        superseded_before_commit = True
                    else:
                        session.model_steps += 1
                        session.usage = _add_usage(session.usage, response.usage)
                        # Keep public partial output for inspection, but never execute partial calls.
                        reason = response.finish_reason.casefold()
                        incomplete = reason not in _COMPLETE_FINISH_REASONS
                        calls = () if incomplete else response.tool_calls
                        session.messages.append(AIMessage(
                            role=MessageRole.ASSISTANT,
                            content=response.text,
                            tool_calls=calls,
                            reasoning=response.reasoning,
                            phase=response.phase,
                        ))
                        rt._record(session, Event.MODEL_RESPONSE, data={
                            **model_identity,
                            "step_id": step.step_id,
                            "text": response.text,
                            "finish_reason": response.finish_reason,
                            "response_id": response.response_id,
                            "model_execution_ms": model_execution_ms,
                            "reasoning_summary": response.visible_reasoning,
                            "tool_calls": [
                                {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                                for c in calls
                            ],
                            "phase": response.phase,
                            "display_phase": "commentary" if calls or response.end_turn is False else "final_answer",
                            "phase_source": "provider" if response.phase is not None else "unknown",
                            "end_turn": response.end_turn,
                            "execution_intent_source": next_execution_action(response).source,
                            "runtime_authored": runtime_authored_commentary,
                            "silent_tool_fallback": runtime_authored_commentary,
                            "compaction_echo_removed": compaction_echo_removed,
                            "usage": {
                                "input_tokens": response.usage.input_tokens,
                                "output_tokens": response.usage.output_tokens,
                                "total_tokens": response.usage.total_tokens,
                            },
                        })

                if cancelled_before_commit:
                    rt._release_step_context(step)
                    rt._cancel_if_requested(session, token)
                    return rt._result(session)
                if superseded_before_commit:
                    rt._release_step_context(step)
                    recovery_instruction = ""
                    recovery_partial = ""
                    recovery_reasoning = ""
                    continue
                if incomplete:
                    raise RuntimeError(f"model response did not complete: {reason}")
                decision = next_execution_action(response)
                if decision.action is ExecutionAction.EXECUTE_TOOLS:
                    session.tool_calls += len(calls)
                    if rt.limits.max_tool_calls > 0 and session.tool_calls > rt.limits.max_tool_calls:
                        session.messages = list(repair_tool_history(
                            session.messages,
                            max_tool_result_chars=rt.limits.max_tool_result_chars,
                        ).messages)
                        return rt._limit(session, "tool call limit reached")
                    session.pending_tool_calls.extend(calls)
                    session.pending_step_id = step.step_id
                    session.pending_bindings = {
                        c.call_id: action_binding_digest(
                            step,
                            tool,
                            c,
                            rt.platform_for_session(session.session_id),
                        )
                        for c in calls
                        if (tool := step.tool_router.get(c.name)) is not None
                    }
                    for call in calls:
                        rt._record(session, Event.TOOL_REQUESTED, data={
                            "call_id": call.call_id,
                            "tool": call.name,
                            "arguments": call.arguments,
                            "step_id": step.step_id,
                        })
                    if not rt._process_pending_tools(session, token, step=step):
                        return rt._result(session)
                    continue
                if decision.action is ExecutionAction.SAMPLE:
                    rt._release_step_context(step)
                    continue
                with rt._active_tokens_guard:
                    if rt._consume_steering(session):
                        rt._release_step_context(step)
                        continue
                    before_turn_completed = getattr(rt, "_before_turn_completed", None)
                    if callable(before_turn_completed):
                        before_turn_completed(session)
                    # Stop accepting steering before committing the terminal state.
                    rt._active_tokens.pop(session.session_id, None)
                session.status = AgentStatus.COMPLETED
                session.final_text = response.text
                session.error = ""
                diff = rt.diff_trackers.snapshot(session.session_id, session.current_turn_id)
                rt.store.save(session)
                rt._record(
                    session,
                    Event.TURN_COMPLETED,
                    data={
                        "text": response.text,
                        "execution_end_source": decision.source,
                        "task_completion": "not_assessed",
                        "stop_decision": stop_decision.as_dict() if stop_decision is not None else None,
                        "completion_check": (
                            "not_configured" if rt.stop_hook is None
                            else "assessed" if stop_decision is not None else "unavailable"),
                        "final_step_id": step.step_id,
                        "diff_revision": diff.revision,
                        "changed_paths": list(diff.paths),
                    },
                )
                rt._release_step_context(step)
                return rt._result(session)
        except ModelCancelled:
            token.cancel()
            rt._cancel_if_requested(session, token)
        except Exception as exc:
            # A cancelled turn raises like any other failure. Reporting it as
            # FAILED loses the distinction the caller acts on, so cancellation is
            # resolved first.
            if token.cancelled:
                rt._cancel_if_requested(session, token)
            else:
                session.status = AgentStatus.FAILED
                session.error = f"{type(exc).__name__}: {exc}"
                # A turn that dies mid tool call leaves calls without results.
                # Carrying that into the next turn poisons the model's history.
                session.messages = list(
                    repair_tool_history(
                        session.messages,
                        max_tool_result_chars=rt.limits.max_tool_result_chars,
                    ).messages
                )
                rt._release_turn_steps(session)
                rt.store.save(session)
                rt._record(session, Event.TURN_FAILED, data={"error": session.error})
        return rt._result(session)
