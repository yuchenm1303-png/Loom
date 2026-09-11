"""The single model/tool state machine used by both core and extended runtimes."""
from __future__ import annotations

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ToolChoice
from app.ai.errors import AITransportError
from app.ai.execution_control import ModelCancelled
from .contracts import AgentEventKind as Event, AgentStatus
from .execution_binding import binding_digest
from .history import repair_tool_history


def _exposed_tool_names(step) -> tuple[str, ...]:
    return tuple(sorted(tool.name for tool in step.tool_router.all()))


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
                for attempt in range(rt.limits.model_retries + 1):
                    step = rt._build_step_context(session, next_model_step=True)
                    messages, extra = rt._prepare_model_request(session, step, token)
                    if len(messages) > rt.limits.max_messages:
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
                    try:
                        response = rt.model_executor.execute(rt.platform, session.profile_id,
                            ChatRequest(messages=tuple(messages), tools=step.tool_router.definitions(),
                                tool_choice=ToolChoice.AUTO, max_output_tokens=rt.limits.output_reserve_tokens,
                                reasoning=reasoning), token)
                        break
                    except AITransportError:
                        if attempt >= rt.limits.model_retries:
                            raise
                        if token._event.wait(min(2.0, 0.25 * 2 ** attempt)):
                            raise ModelCancelled()
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
                incomplete = reason not in {"", "stop", "tool_calls", "function_call", "completed", "end_turn"}
                calls = () if incomplete else response.tool_calls
                session.messages.append(AIMessage(role=MessageRole.ASSISTANT, content=response.text, tool_calls=calls))
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
                    # Stop accepting steering before committing the terminal state.
                    rt._active_tokens.pop(session.session_id, None)
                session.status = AgentStatus.COMPLETED
                rt.store.save(session)
                rt._record(session, Event.TURN_COMPLETED, data={"text": session.final_text})
                return rt._result(session)
        except ModelCancelled:
            session.status = AgentStatus.CANCELLED
            rt.store.save(session)
            rt._record(session, Event.TURN_CANCELLED, data={})
            return rt._result(session)
        except Exception as exc:
            session.status = AgentStatus.FAILED
            session.error = str(exc)
            rt.store.save(session)
            rt._record(session, Event.TURN_FAILED, data={"error": str(exc)})
            return rt._result(session)
