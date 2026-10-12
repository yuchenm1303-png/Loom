"""Request-local, reversible tool identifiers for every provider protocol.

Internal names never become provider API identifiers. Safe existing names stay
unchanged; other names use a deterministic digest, including names in replayed
history. No credentials, durable messages or tool registry entries are mutated.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from .contracts import ChatRequest, ModelResponse, StreamEvent, StreamEventKind
from .errors import AIResponseError

_SAFE = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def wire_identifier(name: str, *, salt: int = 0) -> str:
    if salt == 0 and _SAFE.fullmatch(name):
        return name
    return "loom_tool_" + hashlib.sha256(f"{salt}:{name}".encode()).hexdigest()[:48]


class ToolWireCodec:
    def __init__(self, request: ChatRequest):
        names = {tool.name for tool in request.tools}
        names.update(call.name for message in request.messages for call in message.tool_calls)
        self.forward = {name: name for name in names if _SAFE.fullmatch(name)}
        used = set(self.forward.values())
        for name in sorted(names - self.forward.keys()):
            salt = 0
            while True:
                alias = wire_identifier(name, salt=salt)
                if alias not in used:
                    break
                salt += 1
            self.forward[name] = alias
            used.add(alias)
        self.reverse = {wire: name for name, wire in self.forward.items()}

    def encode(self, request: ChatRequest) -> ChatRequest:
        if all(name == wire for name, wire in self.forward.items()):
            return request
        return replace(request,
            tools=tuple(replace(tool, name=self.forward[tool.name]) for tool in request.tools),
            messages=tuple(replace(message,
                tool_calls=tuple(replace(call, name=self.forward[call.name]) for call in message.tool_calls),
                name=self.forward.get(message.name, message.name),
            ) for message in request.messages))

    def decode(self, response: ModelResponse) -> ModelResponse:
        return replace(response, tool_calls=tuple(
            replace(call, name=self.reverse.get(call.name, call.name)) for call in response.tool_calls))

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
