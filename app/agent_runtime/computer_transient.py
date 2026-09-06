from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Any

from app.ai import ModelResponse, ToolCall


_TRANSIENT_PREFIX = "loom-transient-computer:"


class ComputerTransientInputPlatform:
    """Replace model-produced Computer Use text with one-shot RAM references.

    Runtime v2 persists tool-call arguments before handlers execute. Computer Use
    can type passwords/tokens or carry sensitive task instructions, so raw text must
    not cross that durable boundary. This adapter stashes the raw values in memory
    and substitutes opaque handles before the canonical Runtime sees the response.
    """

    def __init__(self, delegate: Any, *, max_pending: int = 256) -> None:
        self._delegate = delegate
        self._max_pending = max(1, int(max_pending))
        self._lock = threading.RLock()
        self._pending: OrderedDict[str, str] = OrderedDict()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def execute_chat(self, profile_id, request) -> ModelResponse:
        response = self._delegate.execute_chat(profile_id, request)
        if not isinstance(response, ModelResponse) or not response.tool_calls:
            return response

        changed = False
        calls: list[ToolCall] = []
        for call in response.tool_calls:
            arguments = dict(call.arguments)
            call_changed = False

            if call.name == "computer_step" and "instruction" in arguments:
                arguments["instruction"] = self._stash(str(arguments.get("instruction") or ""))
                call_changed = True

            if call.name == "computer_action":
                action = arguments.get("action")
                if isinstance(action, dict) and str(action.get("type") or "") == "type" and "text" in action:
                    action = dict(action)
                    action["text"] = self._stash(str(action.get("text") or ""))
                    arguments["action"] = action
                    call_changed = True

            if call_changed:
                calls.append(ToolCall(call_id=call.call_id, name=call.name, arguments=arguments))
                changed = True
            else:
                calls.append(call)

        if not changed:
            return response
        return ModelResponse(
            text=response.text,
            tool_calls=tuple(calls),
            usage=response.usage,
            finish_reason=response.finish_reason,
            response_id=response.response_id,
        )

    def consume(self, value: str) -> str:
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
        return raw

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def _stash(self, value: str) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._pending[token] = value
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
        return _TRANSIENT_PREFIX + token


__all__ = ["ComputerTransientInputPlatform"]
