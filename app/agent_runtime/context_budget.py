"""Token-aware request budgeting and safe in-turn compaction."""
from __future__ import annotations

import json
import math

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ToolChoice
from .history import repair_tool_history


def estimate_tokens(messages, tools=()) -> int:
    # Conservative UTF-8 estimate, including schemas and image allowance. Providers
    # can replace this with their tokenizer without changing context ownership.
    from .storage import _message_to_dict
    data = [_message_to_dict(m) for m in messages]
    schemas = [{"name": t.name, "description": t.description, "parameters": t.input_schema} for t in tools]
    text = json.dumps([data, schemas], ensure_ascii=False)
    return math.ceil(len(text.encode("utf-8")) / 3) + 8 * len(messages) + sum(4096 for m in messages if m.uses_vision)


def safe_split(messages, keep=12):
    """Retain the latest user and never separate tool calls from their outputs."""
    last_user = max((i for i, m in enumerate(messages) if m.role is MessageRole.USER), default=-1)
    pending = set()
    candidates = []
    for i, message in enumerate(messages):
        pending.update(c.call_id for c in message.tool_calls)
        if message.role is MessageRole.TOOL:
            pending.discard(message.tool_call_id)
        if not pending and 0 < i + 1 <= len(messages) - 2:
            candidates.append(i + 1)
    desired = max(1, len(messages) - keep)
    return max((i for i in candidates if i <= desired), default=0), last_user


def prepare_context(rt, session, step, token):
    envelope = rt._context_envelope(session, step)
    transient = list(rt._request_context_messages(session, step, envelope))
    instructions = rt.instruction_loader.load(session.workspace_dir)
    if instructions:
        transient.append(AIMessage(role=MessageRole.SYSTEM, name="loom_project_instructions", content=instructions))
    tools = step.tool_router.definitions()
    budget = rt.limits.context_window_tokens - rt.limits.output_reserve_tokens
    messages = [*transient, *session.messages]
    if estimate_tokens(messages, tools) <= budget and len(messages) <= rt.limits.max_messages:
        return messages, {"context_digest": envelope.digest}
    repair = repair_tool_history(session.messages, max_tool_result_chars=rt.limits.max_tool_result_chars)
    history = tuple(repair.messages)
    split, last_user = safe_split(history, keep=min(12, max(2, len(history) // 3)))
    if not split:
        raise RuntimeError("context budget exceeded with no safely compactable history")
    archived, retained = history[:split], history[split:]
    # Preserve the verbatim active user instruction even during a single long turn.
    if 0 <= last_user < split:
        retained = (history[last_user], *retained)
    from .context_runtime import _COMPACTION_SYSTEM_PROMPT
    summary_request = ChatRequest(messages=(AIMessage(role=MessageRole.SYSTEM, content=_COMPACTION_SYSTEM_PROMPT), *archived),
        tools=(), tool_choice=ToolChoice.NONE, max_output_tokens=min(2048, rt.limits.output_reserve_tokens))
    if estimate_tokens(summary_request.messages) > budget:
        raise RuntimeError("archived context exceeds compaction request budget; reduce oversized tool output")
    response = rt.model_executor.execute(rt.platform, session.profile_id, summary_request, token)
    if not isinstance(response, ModelResponse):
        raise TypeError("compaction model must return ModelResponse")
    def reject_summary(message):
        from .runtime import _add_usage
        session.usage = _add_usage(session.usage, response.usage)
        raise RuntimeError(message)
    if not response.text.strip() or response.tool_calls or response.finish_reason not in {"", "stop", "completed", "end_turn"}:
        reject_summary("context compaction did not produce a complete summary")
    # Validate fit before committing, so a failed summary cannot replace history.
    candidate = [*transient, AIMessage(role=MessageRole.SYSTEM, content=response.text), *retained]
    if estimate_tokens(candidate, tools) + 256 > budget or len(candidate) > rt.limits.max_messages:
        reject_summary("compacted context still exceeds request budget")
    rt._commit_compaction_locked(session, summary=response.text, repaired=repair, archived=archived,
        retained=retained, summary_source="auto", summary_usage=response.usage)
    return [*transient, *session.messages], {"context_digest": envelope.digest, "auto_compacted": True}
