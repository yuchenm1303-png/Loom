from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Any

from app.ai import ModelResponse, ToolCall


_TRANSIENT_PREFIX = "loom-transient-browser-text:"


class BrowserTransientInputPlatform:
    """Keep browser_type payloads in RAM instead of canonical model history.

    Runtime v2 persists tool-call arguments before handlers execute. Passwords and
    tokens can be bare strings, so pattern matching is not a sufficient persistence
    boundary. Every model-produced browser_type text payload is therefore replaced
    with an opaque one-shot reference before the response reaches durable Runtime.

    The original text exists only in this process, is consumed exactly once by the
    browser_type handler, and is never serialized by this adapter. If the process
    restarts while approval is pending, the reference intentionally becomes invalid
    and the action must be retried rather than recovering a secret from disk.
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
            if call.name != "browser_type" or "text" not in call.arguments:
                calls.append(call)
                continue
            arguments = dict(call.arguments)
            arguments["text"] = self._stash(str(arguments.get("text") or ""))
            calls.append(ToolCall(call_id=call.call_id, name=call.name, arguments=arguments))
            changed = True

        if not changed:
            return response
        return ModelResponse(
            text=response.text,
            tool_calls=tuple(calls),
            usage=response.usage,
            finish_reason=response.finish_reason,
            response_id=response.response_id,
        )

    def consume_browser_type_text(self, value: str) -> str:
        candidate = str(value or "")
        if not candidate.startswith(_TRANSIENT_PREFIX):
            # Direct embedders can call a handler without going through model output.
            # Such values are not written by this adapter and remain the embedder's
            # responsibility; model-produced values always carry the opaque prefix.
            return candidate
        token = candidate[len(_TRANSIENT_PREFIX) :]
        if not token:
            raise RuntimeError("browser transient input reference is malformed")
        with self._lock:
            raw = self._pending.pop(token, None)
        if raw is None:
            raise RuntimeError(
                "browser transient input is no longer available; retry browser_type "
                "instead of recovering typed data from durable state"
            )
        return raw

    def clear_browser_transient_inputs(self) -> None:
        with self._lock:
            self._pending.clear()

    def _stash(self, value: str) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._pending[token] = value
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
        return _TRANSIENT_PREFIX + token


__all__ = ["BrowserTransientInputPlatform"]
