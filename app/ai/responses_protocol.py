"""Responses wire semantics, independent of SDK, provider and agent execution.

Output item IDs, output indexes and function call IDs are separate namespaces.
Only normalized function call IDs leave this decoder. Done events are snapshots,
not additional deltas; validate their prefix and emit only missing suffixes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import StreamEvent, StreamEventKind
from .errors import AIResponseError


@dataclass
class _Call:
    item_id: str = ""
    call_id: str = ""
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)


class ResponsesToolDecoder:
    def __init__(self) -> None:
        self.calls: dict[int, _Call] = {}
        self.items: dict[str, int] = {}
        self.call_ids: dict[str, int] = {}

    def _index(self, *, index: Any, item_id: str = "", call_id: str = "") -> int:
        known = {value for value in (self.items.get(item_id), self.call_ids.get(call_id))
                 if value is not None}
        if index is not None:
            try:
                known.add(int(index))
            except (ValueError, TypeError) as exc:
                raise AIResponseError("Responses output index must be an integer") from exc
        if len(known) != 1:
            raise AIResponseError("Responses tool item has missing or conflicting identity")
        result = known.pop()
        if result < 0:
            raise AIResponseError("Responses output index must not be negative")
        if item_id:
            self.items[item_id] = result
        if call_id:
            self.call_ids[call_id] = result
        return result

    def item(self, item: Any, index: int, *, snapshot: bool) -> list[StreamEvent]:
        item_id = str(getattr(item, "id", "") or "")
        call_id = str(getattr(item, "call_id", "") or "")
        index = self._index(index=index, item_id=item_id, call_id=call_id)
        call = self.calls.setdefault(index, _Call())
        name = str(getattr(item, "name", "") or "")
        if call.item_id and item_id and call.item_id != item_id:
            raise AIResponseError("Responses output item ID changed")
        if call.call_id and call_id and call.call_id != call_id:
            raise AIResponseError("Responses function call ID changed")
        if call.name and name and call.name != name:
            raise AIResponseError("Responses function name changed")
        new_id = call_id if not call.call_id else ""
        new_name = name if not call.name else ""
        call.call_id = call_id or call.call_id
        call.item_id = item_id or call.item_id
        call.name = name or call.name
        events = []
        if new_id or new_name:
            events.append(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
                                      tool_call_index=index, tool_call_id=new_id, tool_name=new_name))
        arguments = str(getattr(item, "arguments", "") or "")
        if arguments or snapshot:
            events.extend(self.arguments(index=index, value=arguments, snapshot=True))
        return events

    def arguments(self, *, index: Any, value: str, item_id: str = "",
                  call_id: str = "", snapshot: bool = False) -> list[StreamEvent]:
        index = self._index(index=index, item_id=item_id, call_id=call_id)
        call = self.calls.get(index)
        if call is None or not call.call_id:
            raise AIResponseError("Responses arguments arrived before their function item")
        if call_id and call_id != call.call_id:
            raise AIResponseError("Responses arguments changed function call ID")
        if snapshot:
            previous = "".join(call.argument_parts)
            if not value.startswith(previous):
                raise AIResponseError("Responses argument snapshot disagrees with streamed deltas")
            suffix = value[len(previous):]
            call.argument_parts = [value]
        else:
            suffix = value
            call.argument_parts.append(value)
        if not suffix:
            return []
        return [StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA, tool_call_index=index,
                            tool_call_id=call.call_id, arguments_delta=suffix)]


def responses_finish_reason(response: Any) -> str:
    status = str(getattr(response, "status", "") or "completed")
    if status == "incomplete":
        reason = str(getattr(getattr(response, "incomplete_details", None), "reason", "") or "")
        if reason == "max_output_tokens":
            return "max_tokens"
        raise AIResponseError(f"Responses output incomplete: {reason or 'unspecified'}")
    if status != "completed":
        raise AIResponseError(f"Responses output ended with status {status}")
    return status


class ResponsesTextDecoder:
    """Reconcile delta/done/terminal snapshots per message content part."""
    def __init__(self) -> None:
        self.parts: dict[tuple[int, int], list[str]] = {}

    def text(self, index: int, content_index: int, value: str, *, snapshot: bool = False) -> list[StreamEvent]:
        key = (index, content_index)
        parts = self.parts.setdefault(key, [])
        if snapshot:
            previous = "".join(parts)
            if not value.startswith(previous):
                raise AIResponseError("Responses text snapshot disagrees with streamed deltas")
            suffix = value[len(previous):]
            self.parts[key] = [value]
        else:
            suffix = value
            parts.append(value)
        return [StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=suffix)] if suffix else []
