from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Any

from app.ai import ModelResponse, ToolCall


_TRANSIENT_PREFIX = "loom-transient-computer:"
_TEXT_FIELDS_BY_TOOL: dict[str, tuple[str, ...]] = {
    "computer_step": ("instruction",),
    "computer_run_task": ("task", "stop_when"),
}

# What a past call's stashed text looks like to the model. A handle in history
# looks like a value, and models copied it into new calls - the largest single
# cause of failed computer_action calls in the field logs and in live runs
# (2026-11-01), each costing a model round trip. This placeholder says what it
# is; it is the same string every time, so the request prefix stays cacheable.
_WITHHELD = "[typed text withheld from history; write the literal text again]"


class ComputerTransientInputPlatform:
    """Replace model-produced Computer Use text with one-shot RAM references.

    Runtime v2 persists tool-call arguments before handlers execute. Computer Use
    can type passwords/tokens or carry sensitive task instructions, so raw text must
    not cross that durable boundary. This adapter stashes the raw values in memory
    and substitutes opaque handles before the canonical Runtime sees the response.
    """

    def __new__(cls, delegate: Any, *, max_pending: int = 256):
        # Runtime construction and model selection may both establish this
        # boundary. Reuse the same owner/stash instead of wrapping handles again.
        if isinstance(delegate, cls):
            return delegate
        return super().__new__(cls)

    def __init__(self, delegate: Any, *, max_pending: int = 256) -> None:
        if delegate is self:
            return
        self._delegate = delegate
        # Other privacy adapters can sit between this boundary and the model.
        # Keep those adapters, but share the existing computer input owner.
        owner = getattr(delegate, "_computer_transient_owner", None)
        self._computer_transient_owner = owner if isinstance(owner, ComputerTransientInputPlatform) else self
        self._max_pending = max(1, int(max_pending))
        self._lock = threading.RLock()
        self._pending: OrderedDict[str, str] = OrderedDict()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def execute_chat(self, profile_id, request) -> ModelResponse:
        response = self._delegate.execute_chat(profile_id, _without_handles(request))
        if self._computer_transient_owner is not self:
            return response
        if not isinstance(response, ModelResponse) or not response.tool_calls:
            return response

        changed = False
        calls: list[ToolCall] = []
        for call in response.tool_calls:
            arguments = dict(call.arguments)
            call_changed = False

            for field in _TEXT_FIELDS_BY_TOOL.get(call.name, ()):
                if field in arguments:
                    arguments[field] = self._stash(str(arguments.get(field) or ""))
                    call_changed = True

            if call.name == "computer_action":
                # Every typed text, wherever the model nested it: a malformed
                # call is rejected by the schema only after this response is
                # durable, so a shape this missed (`actions` inside `action`, a
                # live run 2026-11-01) put the literal text into history.
                stashed = self._stash_typed_text(arguments)
                if stashed is not arguments:
                    arguments = stashed
                    call_changed = True

            if call_changed:
                calls.append(ToolCall(call_id=call.call_id, name=call.name, arguments=arguments))
                changed = True
            else:
                calls.append(call)

        if not changed:
            return response
        from dataclasses import replace
        return replace(response, tool_calls=tuple(calls))

    def consume(self, value: str) -> str:
        if self._computer_transient_owner is not self:
            return self._computer_transient_owner.consume(value)
        candidate = str(value or "")
        if not candidate.startswith(_TRANSIENT_PREFIX):
            return candidate
        token = candidate[len(_TRANSIENT_PREFIX) :]
        if not token:
            raise RuntimeError("computer transient input reference is malformed")
        with self._lock:
            raw = self._pending.pop(token, None)
        if raw is None:
            raise RuntimeError(
                "computer transient input is no longer available; retry the Computer Use action "
                "instead of recovering typed data from durable state"
            )
        if raw.startswith(_TRANSIENT_PREFIX):
            raise RuntimeError("computer input contains a copied transient reference, not literal text; supply a fresh literal test string")
        if _WITHHELD in raw:
            raise RuntimeError(
                "computer input is the history placeholder for earlier typed text, not text to type; nothing was "
                "typed. Write the literal characters to type (Loom never shows typed text back in history)"
            )
        return raw

    def clear(self) -> None:
        if self._computer_transient_owner is not self:
            self._computer_transient_owner.clear()
        with self._lock:
            self._pending.clear()

    def _stash_typed_text(self, value: Any) -> Any:
        """``value`` with every ``{"type": "type", "text": ...}`` text stashed; the same object if none."""

        if isinstance(value, dict):
            changed = False
            result = {}
            for key, item in value.items():
                # A text that is already a handle is stashed too: that is how a
                # copied handle is recognised and refused when consumed.
                if key == "text" and str(value.get("type") or "") == "type":
                    result[key] = self._stash(str(item or ""))
                    changed = True
                else:
                    new = self._stash_typed_text(item)
                    changed = changed or new is not item
                    result[key] = new
            return result if changed else value
        if isinstance(value, list):
            items = [self._stash_typed_text(item) for item in value]
            return items if any(new is not old for new, old in zip(items, value)) else value
        return value

    def _stash(self, value: str) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._pending[token] = value
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
        return _TRANSIENT_PREFIX + token


def _withhold(value: Any) -> Any:
    if isinstance(value, str):
        return _WITHHELD if value.startswith(_TRANSIENT_PREFIX) else value
    if isinstance(value, dict):
        return {key: _withhold(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_withhold(item) for item in value]
    return value


def _without_handles(request: Any) -> Any:
    """The request as the model should see it: past calls' handles replaced by a placeholder.

    Only the outgoing request changes. The session keeps the handles, and every
    call has executed before its message is sent back, so nothing that still
    needs a handle reads this copy.
    """

    from dataclasses import replace

    messages = getattr(request, "messages", None)
    if not messages:
        return request
    changed = False
    projected = []
    for message in messages:
        calls = getattr(message, "tool_calls", ()) or ()
        if calls and any(_TRANSIENT_PREFIX in str(call.arguments) for call in calls):
            message = replace(message, tool_calls=tuple(
                replace(call, arguments=_withhold(call.arguments)) if _TRANSIENT_PREFIX in str(call.arguments) else call
                for call in calls
            ))
            changed = True
        projected.append(message)
    return replace(request, messages=tuple(projected)) if changed else request


__all__ = ["ComputerTransientInputPlatform"]
