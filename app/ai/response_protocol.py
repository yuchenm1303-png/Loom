"""Provider-neutral response boundary: decode wire formats, then validate once.

Codecs are selected by syntax, never by a provider or model name. Calls remain
atomic until completion and use the normal runtime dispatch/approval path.
"""
from __future__ import annotations

from dataclasses import replace
import json
import re
import uuid
from jsonschema import validators

from .contracts import ChatRequest, ModelResponse, ToolCall, ToolChoice
from .errors import AIResponseError, AITruncatedToolCallError
from .tool_policy import exposes_tools
from .tool_protocol import validate_unique_calls

_TAG = re.compile(r'<\s*(/?)\s*[|｜]\s*DSML\s*[|｜]\s*(tool_calls|function_calls|invoke|parameter)\b([^>]*)>')
_PREFIX = re.compile(r'<\s*/?\s*[|｜]\s*DSML\b')
_UNSUPPORTED_WIRE = re.compile(r'(?:<tool_call\b|</tool_call>|<invoke\s+name\s*=|\]\s*<\]\s*minimax\s*\[>\s*\[<)', re.IGNORECASE)


def contains_tool_wire(text: str) -> bool:
    return bool(_PREFIX.search(text) or _UNSUPPORTED_WIRE.search(text))


def _attributes(raw: str, required: set[str]) -> dict[str, str]:
    pairs = re.findall(r'([a-z_]+)\s*=\s*"([^"]*)"', raw)
    remainder = re.sub(r'[a-z_]+\s*=\s*"[^"]*"', '', raw)
    values = dict(pairs)
    if remainder.strip() or len(values) != len(pairs) or set(values) != required:
        raise AIResponseError("tool wire protocol contains invalid attributes")
    return values


def _dsml(text: str, finish_reason: str) -> tuple[str, tuple[ToolCall, ...]]:
    tags = list(_TAG.finditer(text))
    if not tags:
        raise AIResponseError("unrecognized tool wire protocol")
    first = tags[0]
    if first[1] or first[2] not in {"tool_calls", "function_calls"} or first[3].strip():
        raise AIResponseError("tool wire protocol is missing its outer envelope")
    if finish_reason in {"length", "max_tokens"}:
        raise AITruncatedToolCallError(finish_reason=finish_reason, tool_name="wire-protocol", argument_chars=len(text))
    envelope = first[2]
    position = first.end()
    index = 1
    calls = []
    while index < len(tags):
        tag = tags[index]
        if text[position:tag.start()].strip():
            raise AIResponseError("unexpected text inside tool wire envelope")
        if tag[1] and tag[2] == envelope and not tag[3].strip():
            if index != len(tags) - 1 or not calls or text[tag.end():].strip():
                raise AIResponseError("tool wire envelope is empty or has trailing protocol content")
            return text[:first.start()].rstrip(), tuple(calls)
        if tag[1] or tag[2] != "invoke":
            raise AIResponseError("invalid tool wire invocation")
        name = _attributes(tag[3], {"name"})["name"]
        arguments = {}
        position = tag.end()
        index += 1
        while index < len(tags):
            param = tags[index]
            if text[position:param.start()].strip():
                raise AIResponseError("unexpected text inside tool invocation")
            if param[1] and param[2] == "invoke" and not param[3].strip():
                position = param.end()
                index += 1
                break
            if param[1] or param[2] != "parameter" or index + 1 >= len(tags):
                raise AIResponseError("invalid tool wire parameter")
            attrs = _attributes(param[3], {"name", "string"})
            closing = tags[index + 1]
            if not closing[1] or closing[2] != "parameter" or closing[3].strip() or attrs["string"] not in {"true", "false"} or attrs["name"] in arguments:
                raise AIResponseError("invalid or duplicate tool wire parameter")
            raw = text[param.end():closing.start()]
            try:
                arguments[attrs["name"]] = raw if attrs["string"] == "true" else json.loads(raw)
            except json.JSONDecodeError:
                raise AIResponseError("tool wire parameter contains invalid JSON") from None
            position = closing.end()
            index += 2
        else:
            raise AIResponseError("incomplete tool wire invocation")
        calls.append(ToolCall(call_id="wire_" + uuid.uuid4().hex, name=name, arguments=arguments))
    raise AIResponseError("incomplete tool wire envelope")


def normalize_response(request: ChatRequest, response: ModelResponse) -> ModelResponse:
    """Every transport enters the same declaration and completeness checks."""
    if exposes_tools(request) and _PREFIX.search(response.text):
        if response.tool_calls:
            raise AIResponseError("response mixes structured and textual tool calls")
        text, calls = _dsml(response.text, response.finish_reason)
        response = replace(response, text=text, tool_calls=calls, finish_reason="tool_calls")
    if exposes_tools(request) and contains_tool_wire(response.text):
        raise AIResponseError("unrecognized tool wire format; native structured tool calls are required")
    calls = validate_unique_calls(list(response.tool_calls))
    if calls:
        if not exposes_tools(request):
            raise AIResponseError("response called tools that were not exposed")
        if response.finish_reason in {"length", "max_tokens"}:
            raise AITruncatedToolCallError(finish_reason=response.finish_reason, tool_name=calls[0].name, argument_chars=0)
        definitions = {tool.name: tool for tool in request.tools}
        for call in calls:
            if call.name not in definitions:
                raise AIResponseError("response called an undeclared tool")
            validator = validators.validator_for(definitions[call.name].input_schema)(definitions[call.name].input_schema)
            if not validator.is_valid(call.arguments):
                raise AIResponseError("tool arguments do not match the declared schema")
    elif response.finish_reason in {"tool_calls", "function_call", "tool_use"}:
        raise AIResponseError("provider finished with tool intent but no tool calls")
    elif exposes_tools(request) and request.tool_choice is ToolChoice.REQUIRED:
        raise AIResponseError("provider ignored required tool choice")
    return response


class ToolTextBoundary:
    """Hold candidate wire tags across arbitrary fragments before publishing.

    Ordinary text flows immediately except an unfinished angle tag. Once a
    protocol prefix appears, the remainder stays private until atomic parsing.
    """
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.pending = ""
        self.protocol = False

    def feed(self, text: str) -> str:
        if not self.enabled:
            return text
        self.pending += text
        if self.protocol:
            return ""
        match = _PREFIX.search(self.pending) or _UNSUPPORTED_WIRE.search(self.pending)
        if match:
            self.protocol = True
            public = self.pending[:match.start()]
            self.pending = self.pending[match.start():]
            return public
        last = self.pending.rfind("<")
        split = last if last >= 0 and ">" not in self.pending[last:] else len(self.pending)
        public, self.pending = self.pending[:split], self.pending[split:]
        return public

    def finish(self) -> str:
        return "" if self.protocol else self.pending
