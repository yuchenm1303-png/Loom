from __future__ import annotations

from dataclasses import dataclass

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


def repair_tool_history(
    messages: list[AIMessage] | tuple[AIMessage, ...],
    *,
    max_tool_result_chars: int = 20_000,
) -> HistoryRepair:
    """Return a provider-safe history with every tool call/output pair balanced.

    Interrupted runtimes can persist an assistant tool call before its tool result.
    Resuming that transcript unchanged is rejected by some providers and can confuse
    others. Missing outputs are inserted immediately after the assistant message;
    orphan or duplicate tool outputs are removed deterministically.
    """

    source = tuple(messages)
    known_calls: dict[str, str] = {}
    for message in source:
        if message.role is not MessageRole.ASSISTANT:
            continue
        for call in message.tool_calls:
            if call.call_id and call.call_id not in known_calls:
                known_calls[call.call_id] = call.name

    first_outputs: set[str] = set()
    valid_output_ids: set[str] = set()
    removed_orphans = 0
    removed_duplicates = 0
    for message in source:
        if message.role is not MessageRole.TOOL:
            continue
        call_id = str(message.tool_call_id or "").strip()
        if not call_id or call_id not in known_calls:
            removed_orphans += 1
            continue
        if call_id in first_outputs:
            removed_duplicates += 1
            continue
        first_outputs.add(call_id)
        valid_output_ids.add(call_id)

    output: list[AIMessage] = []
    emitted_outputs: set[str] = set()
    inserted = 0
    for message in source:
        if message.role is MessageRole.TOOL:
            call_id = str(message.tool_call_id or "").strip()
            if call_id not in known_calls or call_id in emitted_outputs:
                continue
            emitted_outputs.add(call_id)
            output.append(message)
            continue

        output.append(message)
        if message.role is not MessageRole.ASSISTANT or not message.tool_calls:
            continue
        for call in message.tool_calls:
            if call.call_id in valid_output_ids:
                continue
            result = ToolResult(
                ok=False,
                content=(
                    "Tool execution was aborted because the Loom runtime stopped before "
                    "a durable tool result was recorded."
                ),
                data={"aborted": True, "recovered": True},
            )
            output.append(
                AIMessage(
                    role=MessageRole.TOOL,
                    content=result.model_payload(max_chars=max_tool_result_chars),
                    name=call.name,
                    tool_call_id=call.call_id,
                )
            )
            emitted_outputs.add(call.call_id)
            inserted += 1

    return HistoryRepair(
        messages=tuple(output),
        inserted_aborted_outputs=inserted,
        removed_orphan_outputs=removed_orphans,
        removed_duplicate_outputs=removed_duplicates,
    )


__all__ = ["HistoryRepair", "repair_tool_history"]
