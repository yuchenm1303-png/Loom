from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import AIMessage, MessageRole

from .tools import ToolResult


@dataclass(frozen=True, slots=True)
class HistoryRepair:
    messages: tuple[AIMessage, ...]
    inserted_aborted_outputs: int
    removed_orphan_outputs: int
    removed_duplicate_outputs: int

    @property
    def changed(self) -> bool:
        return bool(
            self.inserted_aborted_outputs
            or self.removed_orphan_outputs
            or self.removed_duplicate_outputs
        )


@dataclass(slots=True)
class _CallGroup:
    message: AIMessage
    answers: dict[str, AIMessage] = field(default_factory=dict)


def repair_tool_history(
    messages: list[AIMessage] | tuple[AIMessage, ...],
    *,
    max_tool_result_chars: int = 20_000,
) -> HistoryRepair:
    """Return a provider-safe history in which every tool call is answered once.

    Outputs pair with calls by position, not by call ID alone: vendors restart IDs
    per response ("call_0" in every turn), so a repeated ID belongs to the nearest
    preceding call that is still unanswered. Each output is emitted directly after
    its assistant message, in call order, because providers reject a tool call
    whose answer does not follow it. A call with no durable answer gets an
    unknown-outcome output; orphan outputs and second answers are dropped.
    """

    source = tuple(messages)
    groups_by_call: dict[str, list[_CallGroup]] = {}
    group_at: dict[int, _CallGroup] = {}
    removed_orphans = 0
    removed_duplicates = 0

    for position, message in enumerate(source):
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            group = _CallGroup(message)
            group_at[position] = group
            for call in message.tool_calls:
                groups_by_call.setdefault(call.call_id, []).append(group)
            continue
        if message.role is not MessageRole.TOOL:
            continue
        call_id = str(message.tool_call_id or "").strip()
        candidates = groups_by_call.get(call_id, [])
        owner = next((group for group in reversed(candidates) if call_id not in group.answers), None)
        if owner is not None:
            owner.answers[call_id] = message
        elif candidates:
            removed_duplicates += 1
        else:
            removed_orphans += 1

    output: list[AIMessage] = []
    inserted = 0
    for position, message in enumerate(source):
        group = group_at.get(position)
        if group is None:
            if message.role is not MessageRole.TOOL:
                output.append(message)
            continue
        output.append(message)
        for call in message.tool_calls:
            answer = group.answers.get(call.call_id)
            if answer is not None:
                output.append(answer)
                continue
            result = ToolResult(
                ok=False,
                content=(
                    "Tool result is unknown because the Loom runtime stopped before a durable result was recorded. "
                    "The action may already have occurred; inspect the affected state before retrying."
                ),
                data={"aborted": True, "recovered": True, "outcome_unknown": True},
            )
            output.append(
                AIMessage(
                    role=MessageRole.TOOL,
                    content=result.model_payload(max_chars=max_tool_result_chars),
                    name=call.name,
                    tool_call_id=call.call_id,
                )
            )
            inserted += 1

    return HistoryRepair(
        messages=tuple(output),
        inserted_aborted_outputs=inserted,
        removed_orphan_outputs=removed_orphans,
        removed_duplicate_outputs=removed_duplicates,
    )


__all__ = ["HistoryRepair", "repair_tool_history"]
