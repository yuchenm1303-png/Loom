"""The single model/tool state machine used by both core and extended runtimes."""
from __future__ import annotations

from dataclasses import replace

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIEmptyResponseError, AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled, ModelSteered

from .contracts import AgentEventKind as Event
from .contracts import AgentStatus
from .execution_binding import action_binding_digest
from .history import repair_tool_history
from .model_replan import revision as steering_revision
from .model_replan import wait_for_signal
from .turn_response_validation import (
    COMPLETE_FINISH_REASONS,
    TERMINAL_RECOVERY_INSTRUCTION,
    TRUNCATED_RECOVERY_INSTRUCTION,
    UNFINISHED_RECOVERY_INSTRUCTION,
    history_message_count,
    invalid_terminal_response,
    strip_compaction_echo,
)


# Compatibility aliases retained for focused tests and callers that imported the
# previous module-private validation helpers.
_COMPLETE_FINISH_REASONS = COMPLETE_FINISH_REASONS
_TERMINAL_RECOVERY_INSTRUCTION = TERMINAL_RECOVERY_INSTRUCTION
_TRUNCATED_RECOVERY_INSTRUCTION = TRUNCATED_RECOVERY_INSTRUCTION
_UNFINISHED_RECOVERY_INSTRUCTION = UNFINISHED_RECOVERY_INSTRUCTION
_invalid_terminal_response = invalid_terminal_response
_strip_compaction_echo = strip_compaction_echo
_history_message_count = history_message_count


def _exposed_tool_names(step) -> tuple[str, ...]:
    return tuple(sorted(tool.name for tool in step.tool_router.all()))


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
                recovery_partial = ""
                attempt = 0
                while True:
                    sample_steering_revision = _consume_steering_for_sample(rt, session, token)
                    # Capture once so request context, advertised tools, and all
                    # tool calls from this response share one immutable world.
                    step = rt._capture_step_context(session, next_model_step=True)
                    messages, extra = rt._prepare_model_request(session, step, token)
                    if _history_message_count(messages) > rt.limits.max_messages:
                        return rt._limit(session, "context message limit reached; no safe compaction boundary")
                    reasoning = step.reasoning
                    profile_id = step.world_state.profile_id
                    tool_names = _exposed_tool_names(step)
                    request_messages = list(messages)
                    if recovery_instruction:
                        if recovery_partial:
                            request_messages.append(AIMessage(
                                role=MessageRole.ASSISTANT,
                                content=recovery_partial,
                            ))
                        request_messages.append(AIMessage(
                            role=MessageRole.SYSTEM,
                            name="loom_terminal_recovery",
                            content=(
                                _UNFINISHED_RECOVERY_INSTRUCTION
                                if recovery_instruction == "unfinished_terminal_text"
                                else _TRUNCATED_RECOVERY_INSTRUCTION
                                if recovery_partial
                                else _TERMINAL_RECOVERY_INSTRUCTION
                            ),
                        ))
                    context_limits = extra.get("context_limits") if isinstance(extra, dict) else None
                    resolved_output_reserve = (
                        int(context_limits.get("output_reserve_tokens"))
                        if isinstance(context_limits, dict) and context_limits.get("output_reserve_tokens")
                        else rt.limits.output_reserve_tokens
                    )
                    request = ChatRequest(
                        messages=tuple(request_messages),
                        tools=step.tool_router.definitions(),
                        tool_choice=ToolChoice.AUTO,
                        max_output_tokens=resolved_output_reserve,
                        reasoning=reasoning,
                    )

                    # Transport retries are retries of this exact request, not a
                    # new semantic sampling step. Keep both the StepContext and
                    # prepared request stable until the provider yields a response.
                    retry_sampling = False
                    while True:
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
                            **extra,
                        })
                        try:
                            response = rt.model_executor.execute(
                                rt.platform,
                                profile_id,
                                request,
                                token,
                                steering_revision=sample_steering_revision,
                            )
                            break
                        except ModelSteered:
                            # The durable inbox contains the new intent. Discard
                            # only this not-yet-committed model sample, keep the
                            # logical turn alive, and rebuild the next request from
                            # history with the steering message included.
                            rt._release_step_context(step)
                            recovery_instruction = ""
                            recovery_partial = ""
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
                            retry_sampling = True
                            break
                        except AIResponseError as exc:
                            session.model_steps += 1
                            rt._record(session, Event.MODEL_RESPONSE_REJECTED, data={
                                "step_id": step.step_id,
                                "reason": "invalid_provider_response",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                                "attempt": attempt,
                                "usage": {
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "total_tokens": 0,
                                },
                            })
                            rt._release_step_context(step)
                            if attempt >= rt.limits.model_retries:
                                raise RuntimeError(
                                    f"model repeatedly returned malformed responses: {exc}"
                                ) from exc
                            attempt += 1
                            recovery_instruction = "invalid_provider_response"
                            recovery_partial = ""
                            retry_sampling = True
                            break
                        except AITransportError as exc:
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
                                rt._release_step_context(step)
                                recovery_instruction = ""
                                recovery_partial = ""
                                retry_sampling = True
                                break
                            attempt = next_attempt
                            continue

                    if retry_sampling:
                        continue
                    if not isinstance(response, ModelResponse):
                        raise TypeError("agent model platform must return ModelResponse")
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
                    if not invalid_terminal:
                        break

                    from .runtime import _add_usage

                    session.model_steps += 1
                    session.usage = _add_usage(session.usage, response.usage)
                    rt._record(session, Event.MODEL_RESPONSE_REJECTED, data={
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
                    if attempt >= rt.limits.model_retries:
                        raise RuntimeError(
                            f"model repeatedly returned an invalid terminal response ({invalid_terminal})"
                        )
                    attempt += 1
                    recovery_instruction = invalid_terminal
                    recovery_partial = (
                        str(response.text or "")
                        if invalid_terminal.startswith("incomplete_finish:")
                        or invalid_terminal == "unfinished_terminal_text"
                        else ""
                    )

                if rt._cancel_if_requested(session, token):
                    return rt._result(session)
                if not isinstance(response, ModelResponse):
                    raise TypeError("agent model platform must return ModelResponse")
                if not response.text and not response.tool_calls:
                    raise RuntimeError("agent model response contained neither text nor tool calls")
                session.model_steps += 1
                from .runtime import _add_usage
                session.usage = _add_usage(session.usage, response.usage)
                # Keep public partial output for inspection, but never execute partial calls.
                reason = response.finish_reason.casefold()
                incomplete = reason not in _COMPLETE_FINISH_REASONS
                calls = () if incomplete else response.tool_calls
                session.messages.append(AIMessage(role=MessageRole.ASSISTANT, content=response.text, tool_calls=calls))
                rt._record(session, Event.MODEL_RESPONSE, data={
                    "step_id": step.step_id,
                    "text": response.text,
                    "finish_reason": response.finish_reason,
                    "response_id": response.response_id,
                    "tool_calls": [
                        {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                        for c in calls
                    ],
                    "compaction_echo_removed": compaction_echo_removed,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                })
                if incomplete:
                    raise RuntimeError(f"model response did not complete: {reason}")
                if calls:
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
                        c.call_id: action_binding_digest(step, tool, c, rt.platform)
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
                with rt._active_tokens_guard:
                    if rt._consume_steering(session):
                        rt._release_step_context(step)
                        continue
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