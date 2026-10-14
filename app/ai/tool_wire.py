"""Request-local, reversible tool identifiers for every provider protocol.

Internal tool names and call IDs never become provider API identifiers. Safe
existing values stay unchanged; other values use a deterministic digest, including
values in replayed history. Names and call IDs live in separate namespaces, so a
call ID can never be mistaken for a tool name. No credentials, durable messages or
tool registry entries are mutated.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from .contracts import ChatRequest, ModelResponse, StreamEvent, StreamEventKind
from .errors import AIResponseError

_SAFE = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
_NAME_PREFIX = "loom_tool_"
_CALL_PREFIX = "loom_call_"


def wire_identifier(name: str, *, salt: int = 0, prefix: str = _NAME_PREFIX) -> str:
    if salt == 0 and _SAFE.fullmatch(name):
        return name
    return prefix + hashlib.sha256(f"{salt}:{name}".encode()).hexdigest()[:48]


def _assign(values: set[str], *, prefix: str) -> tuple[dict[str, str], dict[str, str]]:
    """Give every value a distinct wire-safe identifier; safe values map to themselves."""
    forward = {value: value for value in values if _SAFE.fullmatch(value)}
    used = set(forward.values())
    for value in sorted(values - forward.keys()):
        salt = 0
        while True:
            alias = wire_identifier(value, salt=salt, prefix=prefix)
            if alias not in used:
                break
            salt += 1
        forward[value] = alias
        used.add(alias)
    return forward, {wire: value for value, wire in forward.items()}


class ToolWireCodec:
    def __init__(self, request: ChatRequest):
        names = {tool.name for tool in request.tools}
        names.update(call.name for message in request.messages for call in message.tool_calls)
        self.forward, self.reverse = _assign(names, prefix=_NAME_PREFIX)
        # Vendors restart call IDs per response (for example "call_0" in every turn)
        # and some use characters that strict Messages-style APIs reject. Both
        # directions are request-local, so a repeated ID keeps one alias per request.
        call_ids = {call.call_id for message in request.messages for call in message.tool_calls}
        call_ids.update(message.tool_call_id for message in request.messages if message.tool_call_id)
        self.call_forward, self.call_reverse = _assign(call_ids, prefix=_CALL_PREFIX)

    def _identity(self) -> bool:
        return all(name == wire for name, wire in self.forward.items()) and all(
            call_id == wire for call_id, wire in self.call_forward.items())

    def encode(self, request: ChatRequest) -> ChatRequest:
        if self._identity():
            return request
        return replace(request,
            tools=tuple(replace(tool, name=self.forward[tool.name]) for tool in request.tools),
            messages=tuple(replace(message,
                tool_calls=tuple(
                    replace(call, name=self.forward[call.name], call_id=self.call_forward[call.call_id])
                    for call in message.tool_calls),
                name=self.forward.get(message.name, message.name),
                tool_call_id=self.call_forward.get(message.tool_call_id, message.tool_call_id),
            ) for message in request.messages))

    def decode(self, response: ModelResponse) -> ModelResponse:
        return replace(response, tool_calls=tuple(
            replace(call, name=self.reverse.get(call.name, call.name),
                    call_id=self.call_reverse.get(call.call_id, call.call_id))
            for call in response.tool_calls))

    def decode_contract_error(self, error) -> None:
        """Return a contract error's response and call IDs to the caller's internal names."""
        response = getattr(error, "response", None)
        if response is not None:
            error.response = self.decode(response)
        error.invalid_call_ids = tuple(self.call_reverse.get(call_id, call_id)
                                       for call_id in getattr(error, "invalid_call_ids", ()))

    def stream_decoder(self):
        return ToolStreamNames(self)


class ToolStreamNames:
    """Accumulate split names without leaking partial aliases to dispatch/UI."""
    def __init__(self, codec: ToolWireCodec):
        self.codec = codec
        self.names: dict[object, str] = {}
        self.ids: dict[str, object] = {}
        self.emitted: dict[object, str] = {}
        self.last_key: object = 0

    def decode(self, event: StreamEvent) -> StreamEvent:
        if all(name == wire for name, wire in self.codec.forward.items()):
            return event
        if event.kind is not StreamEventKind.TOOL_CALL_DELTA:
            return event
        key = event.tool_call_index
        if key is None:
            key = self.ids.get(event.tool_call_id, event.tool_call_id or self.last_key)
        self.last_key = key
        if event.tool_call_id:
            self.ids[event.tool_call_id] = key
        name = self.names.get(key, "")
        fragment = event.tool_name
        if fragment:
            if fragment != name:
                name = fragment if name and fragment.startswith(name) else name + fragment
            self.names[key] = name
        ambiguous = any(wire != name and wire.startswith(name) for wire in self.codec.reverse)
        if name in self.codec.reverse and (not ambiguous or event.arguments_delta):
            decoded = self.codec.reverse[name]
            if key in self.emitted and self.emitted[key] != decoded:
                raise AIResponseError("tool call stream changed its function name")
            if key not in self.emitted:
                self.emitted[key] = decoded
                return replace(event, tool_name=decoded)
        return replace(event, tool_name="")
