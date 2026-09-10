from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, TextIO

from app.ai import ReasoningRequest
from app.agent_runtime import PermissionMode
from app.agent_runtime.tools import ToolExposure, ToolRegistry
from app.settings import LoomSettingsStore

from .app_server_thread_management import (
    ManagedStreamingJsonRpcStdioServer,
    ManagedStreamingLoomAppServerService,
    ManagedStreamingLoomRpcController,
)


_CAPABILITY_MATCHERS: dict[str, Callable[[str], bool]] = {
    "computerUse": lambda name: name.startswith("computer_"),
    "browserUse": lambda name: name.startswith("browser_"),
    "webSearch": lambda name: name.startswith("web_search"),
    "mcp": lambda name: name.startswith("mcp."),
    "skills": lambda name: name.startswith("skill_"),
    "toolSearch": lambda name: name == "tool_search",
    "codeMode": lambda name: name == "code_mode",
}


class ReasoningManagedLoomAppServerService(ManagedStreamingLoomAppServerService):
    """Managed App Server with reasoning, expressions, and desktop settings."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        root = Path(getattr(self.store, "root", "")).expanduser().resolve()
        try:
            runtime_home = root.parents[1]
        except IndexError:
            runtime_home = root.parent
        self.settings_store = LoomSettingsStore(runtime_home)
        # Keep the canonical runtime tool set so capability switches can hide or
        # restore whole tool families without rebuilding the model/runtime stack.
        self._canonical_tools = tuple(self.runtime.tools.all())
        self._apply_capability_settings(self.settings_store.snapshot())

    def _apply_capability_settings(self, settings: dict[str, Any]) -> None:
        raw = settings.get("capabilities")
        capabilities = dict(raw) if isinstance(raw, dict) else {}
        rebuilt = []
        for original in self._canonical_tools:
            hidden = any(
                not bool(capabilities.get(key, True)) and matcher(original.name)
                for key, matcher in _CAPABILITY_MATCHERS.items()
            )
            rebuilt.append(replace(original, exposure=ToolExposure.HIDDEN) if hidden else original)
        self.runtime.tools = ToolRegistry(tuple(rebuilt))

    @staticmethod
    def _safe_status(runtime: Any, method_name: str) -> dict[str, Any]:
        method = getattr(runtime, method_name, None)
        if not callable(method):
            return {"available": False}
        try:
            value = method()
        except TypeError:
            try:
                value = method(None)
            except Exception as exc:
                return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        payload = dict(value) if isinstance(value, dict) else {}
        payload.setdefault("available", True)
        return payload

    def _capability_status(self, settings: dict[str, Any]) -> dict[str, Any]:
        raw = settings.get("capabilities")
        preferences = dict(raw) if isinstance(raw, dict) else {}

        computer = self._safe_status(self.runtime, "computer_status")
        browser = self._safe_status(self.runtime, "browser_status")
        web_search = self._safe_status(self.runtime, "web_search_status")
        mcp = self._safe_status(self.runtime, "mcp_status")

        skill_manager = getattr(self.runtime, "skill_manager", None)
        skills: dict[str, Any] = {"available": skill_manager is not None, "count": 0, "errors": []}
        if skill_manager is not None:
            try:
                snapshot = skill_manager.discover(self.default_workspace)
                skills["count"] = len(snapshot.skills)
                skills["errors"] = list(snapshot.errors)
            except Exception as exc:
                skills["error"] = f"{type(exc).__name__}: {exc}"

        canonical_names = {tool.name for tool in self._canonical_tools}
        tool_search = {"available": "tool_search" in canonical_names}
        code_mode = {"available": "code_mode" in canonical_names}

        statuses = {
            "computerUse": computer,
            "browserUse": browser,
            "webSearch": web_search,
            "mcp": mcp,
            "skills": skills,
            "toolSearch": tool_search,
            "codeMode": code_mode,
        }
        for key, payload in statuses.items():
            user_enabled = bool(preferences.get(key, True))
            backend_enabled = payload.get("enabled")
            if backend_enabled is None:
                backend_enabled = payload.get("available", False)
            payload["userEnabled"] = user_enabled
            payload["active"] = bool(user_enabled and backend_enabled)
        return statuses

    def runtime_status(self) -> dict[str, Any]:
        status = super().runtime_status()
        reasoning = getattr(self.runtime, "reasoning", None)
        capability = getattr(self.runtime, "reasoning_capability", None)
        status["reasoning"] = reasoning.as_safe_dict() if reasoning is not None else None
        status["reasoningCapability"] = dict(capability) if isinstance(capability, dict) else None
        getter = getattr(self.runtime, "get_sticker_preferences", None)
        status["stickerPreferences"] = getter() if callable(getter) else None
        settings = self.settings_store.snapshot()
        status["settings"] = settings
        status["capabilityStatus"] = self._capability_status(settings)
        status["registeredToolCount"] = len(self._canonical_tools)
        status["exposedToolCount"] = len(self.runtime.tools.router().all())
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

    def settings_get(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"settings": self.settings_store.snapshot(), "runtime": self.runtime_status()}

    def settings_set(self, params: dict[str, Any]) -> dict[str, Any]:
        status = super().runtime_status()
        active = list(status.get("activeThreadIds") or [])
        if active:
            raise RuntimeError("finish or stop the current turn before changing capabilities")
        capability = str(params.get("capability") or "").strip()
        enabled = params.get("enabled")
        if not capability:
            raise ValueError("capability is required")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        settings = self.settings_store.set_capability(capability, enabled)
        self._apply_capability_settings(settings)
        updated = self.runtime_status()
        self._notify(
            "runtime/updated",
            {
                "reason": "capability_setting_changed",
                "runtime": updated,
            },
        )
        return {"settings": settings, "runtime": updated}


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
        result["capabilities"]["settings"] = {
            "read": True,
            "updateCapabilities": True,
            "requiresIdleTurn": True,
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
        if method == "settings/get":
            return self.service.settings_get(params)
        if method == "settings/set":
            return self.service.settings_set(params)
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
