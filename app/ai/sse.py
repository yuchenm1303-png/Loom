"""Decode SSE data envelopes; JSON semantics belong to the protocol adapter."""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from .errors import AIResponseError


def json_sse_events(lines: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    parts: list[str] = []
    for raw in lines:
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise AIResponseError("Messages SSE contains invalid UTF-8") from exc
        if line == "":
            if parts:
                yield from _parse(parts)
                parts = []
            continue
        if line.startswith("data:"):
            value = line[5:]
            parts.append(value[1:] if value.startswith(" ") else value)
    if parts:
        yield from _parse(parts)


def _parse(parts: list[str]) -> Iterator[dict[str, Any]]:
    data = "\n".join(parts)
    if not data or data == "[DONE]":
        return
    try:
        event = json.loads(data)
    except ValueError as exc:
        raise AIResponseError("Messages stream contains invalid SSE JSON") from exc
    if not isinstance(event, dict):
        raise AIResponseError("Messages SSE event must be an object")
    yield event
