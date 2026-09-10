"""Token-aware request budgeting and safe in-turn compaction."""
from __future__ import annotations

import json
import math

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ToolChoice
from .history import repair_tool_history


# Codex treats compaction as a normal model turn and considers it successful once
# the provider completes the response. It does not maintain an allow-list of
# provider-specific finish-reason strings. Keep the same boundary here: a
# non-empty assistant response with no tool call is usable compaction output.
_COMPACTION_RETRIES = 2


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


def _safe_front_boundary(messages) -> int:
    """Return the first front-trim boundary that preserves tool call/result groups."""
    pending = set()
    for i, message in enumerate(messages):
        pending.update(call.call_id for call in message.tool_calls)
        if message.role is MessageRole.TOOL:
            pending.discard(message.tool_call_id)
        if not pending:
            return i + 1
    return 0


def _trim_compaction_input_to_budget(messages, *, budget: int, max_output_tokens: int):
    """Mirror Codex's recovery: drop oldest compact-input items until it fits.

    Open-source Codex retries a compaction request after removing the oldest
    history item when that request exceeds the model context window. Loom uses
    Chat Completions, so we additionally trim only at a complete tool boundary.
    The canonical messages being removed from the summarizer are still archived
    in Loom's checkpoint; this only bounds the helper request sent to the model.
    """
    from .context_runtime import _COMPACTION_SYSTEM_PROMPT

    compact_input = tuple(messages)
    while compact_input:
        request = ChatRequest(
            messages=(
                AIMessage(role=MessageRole.SYSTEM, content=_COMPACTION_SYSTEM_PROMPT),
                *compact_input,
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            max_output_tokens=max_output_tokens,
        )
        if estimate_tokens(request.messages) <= budget:
            return request
        boundary = _safe_front_boundary(compact_input)
        if boundary <= 0 or boundary >= len(compact_input):
            break
        compact_input = compact_input[boundary:]
    raise RuntimeError("context compaction input exceeds request budget after trimming oldest history")


def _partition_candidates(history):
    """Yield increasingly aggressive safe partitions, keeping recent work intact."""
    keeps = []
    initial = min(12, max(2, len(history) // 3))
    for keep in (initial, max(2, initial // 2), 2):
        if keep in keeps:
            continue
        keeps.append(keep)
        split, last_user = safe_split(history, keep=keep)
        if not split:
            continue
        archived = tuple(history[:split])
        retained = tuple(history[split:])
        # Preserve the active user instruction verbatim even if a long tool turn
        # forces its canonical copy onto the archived side of the boundary.
        if 0 <= last_user < split:
            retained = (history[last_user], *retained)
        yield archived, retained


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
    partitions = tuple(_partition_candidates(history))
    if not partitions:
        raise RuntimeError("context budget exceeded with no safely compactable history")

    # The old implementation capped compaction at 2048 output tokens and then
    # rejected provider values such as `length` or `eos_token`. Codex instead
    # waits for the provider's completed response and uses the resulting assistant
    # message. Give the helper turn the normal reserved output budget and do not
    # reinterpret provider-specific finish-reason vocabulary here.
    max_output_tokens = max(1, rt.limits.output_reserve_tokens)
    last_error = "context compaction failed"

    for archived, retained in partitions:
        candidate_without_summary = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content="context summary"),
            *retained,
        ]
        if estimate_tokens(candidate_without_summary, tools) + 256 > budget:
            last_error = "compacted context still exceeds request budget"
            continue
        if len(candidate_without_summary) > rt.limits.max_messages:
            last_error = "compacted context still exceeds message limit"
            continue

        summary_request = _trim_compaction_input_to_budget(
            archived,
            budget=budget,
            max_output_tokens=max_output_tokens,
        )

        for attempt in range(_COMPACTION_RETRIES + 1):
            response = rt.model_executor.execute(rt.platform, session.profile_id, summary_request, token)
            if not isinstance(response, ModelResponse):
                raise TypeError("compaction model must return ModelResponse")

            # Match the Codex completion boundary: once the model request itself
            # completed, finish_reason is provider metadata rather than a second
            # protocol gate. In particular, a useful non-empty summary returned
            # with `length`, `eos_token`, `finished`, etc. must not kill the turn.
            summary = str(response.text or "").strip()
            if summary and not response.tool_calls:
                candidate = [
                    *transient,
                    AIMessage(role=MessageRole.SYSTEM, content=summary),
                    *retained,
                ]
                if (
                    estimate_tokens(candidate, tools) + 256 <= budget
                    and len(candidate) <= rt.limits.max_messages
                ):
                    rt._commit_compaction_locked(
                        session,
                        summary=summary,
                        repaired=repair,
                        archived=archived,
                        retained=retained,
                        summary_source="auto" if attempt == 0 else "auto_retry",
                        summary_usage=response.usage,
                    )
                    return [*transient, *session.messages], {
                        "context_digest": envelope.digest,
                        "auto_compacted": True,
                        "compaction_attempts": attempt + 1,
                    }
                last_error = "compacted context still exceeds request budget"
                break

            last_error = (
                "context compaction model returned unexpected tool calls"
                if response.tool_calls
                else "context compaction model returned an empty summary"
            )

        # If a valid summary cannot fit with this retained suffix, try the next
        # more aggressive safe split. This is the Chat-Completions equivalent of
        # Codex trimming old history and retrying its compact turn.

    raise RuntimeError(last_error)
