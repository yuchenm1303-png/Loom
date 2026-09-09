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
    """Managed App Server with Codex-style live reasoning selection."""

    def runtime_status(self) -> dict[str, Any]:
        status = super().runtime_status()
        reasoning = getattr(self.runtime, "reasoning", None)
        status["reasoning"] = reasoning.as_safe_dict() if reasoning is not None else None
        return status

    def runtime_set_reasoning(self, params: dict[str, Any]) -> dict[str, Any]:
        status = super().runtime_status()
        active = list(status.get("activeThreadIds") or [])
        if active:
            raise RuntimeError("finish or stop the current turn before changing reasoning")

        reasoning = ReasoningRequest.from_values(params.get("kind"), params.get("value"))
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


class ReasoningManagedLoomRpcController(ManagedStreamingLoomRpcController):
    service: ReasoningManagedLoomAppServerService

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        result["capabilities"]["reasoningControl"] = {
            "read": True,
            "update": True,
            "modelSpecific": True,
        }
        notifications = result["capabilities"].setdefault("notifications", [])
        if "runtime/updated" not in notifications:
            notifications.append("runtime/updated")
        return result

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "runtime/set_reasoning":
            return self.service.runtime_set_reasoning(params)
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
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
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
