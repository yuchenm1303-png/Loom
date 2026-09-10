from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

from app.ai import ReasoningRequest
from app.agent_runtime import PermissionMode

from .app_server_thread_management import (
    ManagedStreamingJsonRpcStdioServer,
    ManagedStreamingLoomAppServerService,
    ManagedStreamingLoomRpcController,
)


class ReasoningManagedLoomAppServerService(ManagedStreamingLoomAppServerService):
    """Managed App Server with Codex-style reasoning and chat-expression controls."""

    def runtime_status(self) -> dict[str, Any]:
        status = super().runtime_status()
        reasoning = getattr(self.runtime, "reasoning", None)
        capability = getattr(self.runtime, "reasoning_capability", None)
        status["reasoning"] = reasoning.as_safe_dict() if reasoning is not None else None
        status["reasoningCapability"] = dict(capability) if isinstance(capability, dict) else None
        getter = getattr(self.runtime, "get_sticker_preferences", None)
        status["stickerPreferences"] = getter() if callable(getter) else None
        return status

    def runtime_set_reasoning(self, params: dict[str, Any]) -> dict[str, Any]:
        status = super().runtime_status()
        active = list(status.get("activeThreadIds") or [])
        if active:
            raise RuntimeError("finish or stop the current turn before changing reasoning")

        capability = getattr(self.runtime, "reasoning_capability", None)
        if not isinstance(capability, dict):
            raise ValueError("the current model does not advertise reasoning controls")

        reasoning = ReasoningRequest.from_values(params.get("kind"), params.get("value"))
        if reasoning is None:
            raise ValueError("reasoning kind and value are required")
        expected_kind = str(capability.get("kind") or "")
        if reasoning.kind.value != expected_kind:
            raise ValueError("reasoning kind is not supported by the current model")
        supported = {
            str(option.get("value") or "")
            for option in capability.get("options") or []
            if isinstance(option, dict)
        }
        if reasoning.value not in supported:
            raise ValueError(
                f"reasoning value {reasoning.value!r} is not supported by the current model"
            )

        self.runtime.reasoning = reasoning
        updated = self.runtime_status()
        self._notify(
            "runtime/updated",
            {
                "reason": "reasoning_changed",
                "runtime": updated,
            },
        )
        return updated

    def sticker_preferences_get(self, _params: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self.runtime, "get_sticker_preferences", None)
        if not callable(getter):
            raise ValueError("the current runtime does not expose sticker preferences")
        return {"preferences": getter()}

    def sticker_preferences_set(self, params: dict[str, Any]) -> dict[str, Any]:
        status = super().runtime_status()
        active = list(status.get("activeThreadIds") or [])
        if active:
            raise RuntimeError("finish or stop the current turn before changing sticker preferences")

        setter = getattr(self.runtime, "set_sticker_preferences", None)
        if not callable(setter):
            raise ValueError("the current runtime does not expose sticker preferences")
        raw = params.get("preferences", params)
        if not isinstance(raw, dict):
            raise ValueError("sticker preferences must be an object")
        preferences = setter(raw)
        updated = self.runtime_status()
        self._notify(
            "runtime/updated",
            {
                "reason": "sticker_preferences_changed",
                "runtime": updated,
            },
        )
        return {"preferences": preferences, "runtime": updated}


class ReasoningManagedLoomRpcController(ManagedStreamingLoomRpcController):
    service: ReasoningManagedLoomAppServerService

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        result["capabilities"]["reasoningControl"] = {
            "read": True,
            "update": True,
            "modelSpecific": True,
        }
        result["capabilities"]["stickerPreferences"] = {
            "read": True,
            "update": True,
            "schema": "ai_ledger_chat_expression_preferences_v2",
        }
        notifications = result["capabilities"].setdefault("notifications", [])
        if "runtime/updated" not in notifications:
            notifications.append("runtime/updated")
        return result

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "runtime/set_reasoning":
            return self.service.runtime_set_reasoning(params)
        if method == "sticker/preferences/get":
            return self.service.sticker_preferences_get(params)
        if method == "sticker/preferences/set":
            return self.service.sticker_preferences_set(params)
        return super()._dispatch(method, params)


class ReasoningManagedJsonRpcStdioServer(ManagedStreamingJsonRpcStdioServer):
    def __init__(self, service: ReasoningManagedLoomAppServerService, **kwargs: Any) -> None:
        super().__init__(service, **kwargs)
        self.controller = ReasoningManagedLoomRpcController(service)


def serve_reasoning_managed_streaming_stdio(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    vision: bool = True,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    # The launcher passes the active model's vision capability so downstream
    # request handlers can refuse attachments when the model is text-only.
    # Older callers omit it; default to ``True`` so the capability is opt-out,
    # not opt-in.
    setattr(runtime, "supports_vision", bool(vision))
    service = ReasoningManagedLoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
    )
    server = ReasoningManagedJsonRpcStdioServer(service)
    try:
        return server.serve(reader=reader, writer=writer)
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = [
    "ReasoningManagedJsonRpcStdioServer",
    "ReasoningManagedLoomAppServerService",
    "ReasoningManagedLoomRpcController",
    "serve_reasoning_managed_streaming_stdio",
]
