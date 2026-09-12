"""The single model/tool state machine used by both core and extended runtimes."""
from __future__ import annotations

import re

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIEmptyResponseError, AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled

from .contracts import AgentEventKind as Event
from .contracts import AgentStatus
from .execution_binding import binding_digest
from .history import repair_tool_history


def _exposed_tool_names(step) -> tuple[str, ...]:
    return tuple(sorted(tool.name for tool in step.tool_router.all()))


_COMPLETE_FINISH_REASONS = {"", "stop", "tool_calls", "function_call", "completed", "end_turn"}
_COMPLETE_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_DANGLING_TERMINAL_RE = re.compile(r"(?:\[|\{|<tool_call>|```(?:json)?)\s*$", re.IGNORECASE)
_TERMINAL_RECOVERY_INSTRUCTION = (
    "Your previous response was rejected because it was empty, malformed (including invalid native tool-call "
    "arguments), ended with an incomplete serialized structure, or contained reasoning without a user-visible "
    "answer. Continue the same task now. "
    "If an available tool is needed, emit a native structured tool call through the tool-calling protocol; "
    "do not print JSON, '[' or a tool-call prefix in assistant text. Otherwise return a complete final answer."
)


def _invalid_terminal_response(response: ModelResponse) -> str:
    """Reject provider 'stop' responses that cannot be valid terminal output."""
    reason = str(response.finish_reason or "").strip().casefold()
    if response.tool_calls or reason not in _COMPLETE_FINISH_REASONS:
        return ""
    raw = str(response.text or "")
    visible = _COMPLETE_THINK_BLOCK_RE.sub("", raw).strip()
    if raw.strip() and not visible:
        return "reasoning_without_visible_answer"
    if _DANGLING_TERMINAL_RE.search(visible):
        return "dangling_serialized_structure"
    if visible.count("```") % 2:
        return "unterminated_code_fence"
    return ""


def _history_message_count(messages) -> int:
    """Count conversation messages, ignoring Loom's own injected guidance."""
    total = 0
    for message in messages:
        if message.role is MessageRole.SYSTEM and str(getattr(message, "name", "") or "").startswith("loom_"):
            continue
        total += 1
    return total


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
                for attempt in range(rt.limits.model_retries + 1):
                    step = rt._build_step_context(session, next_model_step=True)
                    messages, extra = rt._prepare_model_request(session, step, token)
                    if _history_message_count(messages) > rt.limits.max_messages:
                        return rt._limit(session, "context message limit reached; no safe compaction boundary")
                    reasoning = getattr(rt, "reasoning", None)
                    tool_names = _exposed_tool_names(step)
                    rt._record(session, Event.MODEL_REQUESTED, data={
                        "profile_id": session.profile_id, "step": step.model_step,
                        "step_id": step.step_id, "message_count": len(messages),
                        "tool_count": len(tool_names), "tool_names": list(tool_names),
                        "permission_mode": step.world_state.permission_mode.value,
                        "reasoning": reasoning.as_safe_dict() if reasoning is not None else None,
                        "attempt": attempt, **extra,
                    })
                    request_messages = list(messages)
                    if recovery_instruction:
                        request_messages.append(AIMessage(
                            role=MessageRole.SYSTEM,
                            name="loom_terminal_recovery",
                            content=_TERMINAL_RECOVERY_INSTRUCTION,
                        ))
                    context_limits = extra.get("context_limits") if isinstance(extra, dict) else None
                    resolved_output_reserve = (
                        int(context_limits.get("output_reserve_tokens"))
                        if isinstance(context_limits, dict) and context_limits.get("output_reserve_tokens")
                        else rt.limits.output_reserve_tokens
                    )
                    try:
                        response = rt.model_executor.execute(rt.platform, session.profile_id,
                            ChatRequest(messages=tuple(request_messages), tools=step.tool_router.definitions(),
                                tool_choice=ToolChoice.AUTO, max_output_tokens=resolved_output_reserve,
                                reasoning=reasoning), token)
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
                        if attempt >= rt.limits.model_retries:
                            raise RuntimeError(
                                "model repeatedly completed without public text or tool calls"
                            ) from exc
                        recovery_instruction = "empty_response"
                        continue
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
                        if attempt >= rt.limits.model_retries:
                            raise RuntimeError(
                                f"model repeatedly returned malformed responses: {exc}"
                            ) from exc
                        recovery_instruction = "invalid_provider_response"
                        continue
                    except AITransportError as exc:
                        if not exc.retryable or attempt >= rt.limits.model_retries:
                            raise
                        if token._event.wait(min(2.0, 0.25 * 2 ** attempt)):
                            raise ModelCancelled()
                        continue

                    if not isinstance(response, ModelResponse):
                        raise TypeError("agent model platform must return ModelResponse")
                    invalid_terminal = _invalid_terminal_response(response)
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
                    if attempt >= rt.limits.model_retries:
                        raise RuntimeError(
                            f"model repeatedly returned an invalid terminal response ({invalid_terminal})"
                        )
                    recovery_instruction = invalid_terminal
                if rt._cancel_if_requested(session, token):
                    return rt._result(session)
                if not isinstance(response, ModelResponse):
                    raise TypeError("agent model platform must return ModelResponse")
                if not response.text and not response.tool_calls:
                    raise RuntimeError("agent model response contained neither text nor tool calls")
                session.model_steps += 1
                from .runtime import _add_usage
                session.usage = _add_usage(session.usage, response.usage)
                reason = response.finish_reason.casefold()
                incomplete = reason not in {"", "stop", "tool_calls", "function_call", "completed", "end_turn"}
                calls = () if incomplete else response.tool_calls
                # Preserve provider-private reasoning continuity in canonical
                # model history without exposing it through MODEL_RESPONSE.
                session.messages.append(AIMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.text,
                    tool_calls=calls,
                    reasoning_content=response.reasoning_content,
                ))
                rt._record(session, Event.MODEL_RESPONSE, data={
                    "step_id": step.step_id, "text": response.text, "finish_reason": response.finish_reason,
                    "response_id": response.response_id,
                    "tool_calls": [{"call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in calls],
                    "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                        "total_tokens": response.usage.total_tokens},
                })
                if incomplete:
                    raise RuntimeError(f"model response did not complete: {reason}")
                if calls:
                    session.tool_calls += len(calls)
                    if rt.limits.max_tool_calls > 0 and session.tool_calls > rt.limits.max_tool_calls:
                        session.messages = list(repair_tool_history(session.messages,
                            max_tool_result_chars=rt.limits.max_tool_result_chars).messages)
                        return rt._limit(session, "tool call limit reached")
                    session.pending_tool_calls.extend(calls)
                    session.pending_step_id = step.step_id
                    session.pending_bindings = {c.call_id: binding_digest(step, tool, rt.platform)
                        for c in calls if (tool := step.tool_router.get(c.name)) is not None}
                    for call in calls:
                        rt._record(session, Event.TOOL_REQUESTED, data={"call_id": call.call_id,
                            "tool": call.name, "arguments": call.arguments, "step_id": step.step_id})
                    if not rt._process_pending_tools(session, token, step=step):
                        return rt._result(session)
                    continue
                with rt._active_tokens_guard:
                    if rt._consume_steering(session):
                        continue
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
                return rt._result(session)
        except ModelCancelled:
            token.cancel()
            rt._cancel_if_requested(session, token)
        except Exception as exc:
            if token.cancelled:
                rt._cancel_if_requested(session, token)
            else:
                session.status = AgentStatus.FAILED
                session.error = f"{type(exc).__name__}: {exc}"
                session.messages = list(
                    repair_tool_history(
                        session.messages,
                        max_tool_result_chars=rt.limits.max_tool_result_chars,
                    ).messages
                )
                rt.store.save(session)
                rt._record(session, Event.TURN_FAILED, data={"error": session.error})
        return rt._result(session)
