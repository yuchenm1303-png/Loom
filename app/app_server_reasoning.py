from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, TextIO

from app.ai import ReasoningRequest
from app.agent_runtime import AgentEvent, AgentEventKind, AgentStatus, PermissionMode
from app.agent_runtime.tools import ToolExposure, ToolRegistry
from app.runtime_model_switch import build_runtime_model_platform, validate_runtime_reasoning
from app.settings import SETTINGS_UPDATE_PREFIX, LoomSettingsStore

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


def _is_browser_setting(capability: str) -> bool:
    """Detect a browser preference inside the desktop settings-update envelope."""

    raw = str(capability or "").strip()
    if not raw.startswith(SETTINGS_UPDATE_PREFIX):
        return False
    try:
        payload = json.loads(raw[len(SETTINGS_UPDATE_PREFIX):])
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return str(payload.get("path") or "").startswith("browser.")


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
        snapshot = self.settings_store.snapshot()
        self._apply_capability_settings(snapshot)
        self._apply_browser_settings(snapshot)

    def _apply_browser_settings(self, settings: dict[str, Any]) -> str:
        """Point the browser layer at whichever browser the user selected.

        Returns an empty string on success, or a reason the stored choice could
        not be honoured. Startup must not abort over it: a saved cdp-attach
        endpoint whose browser is no longer running, or an extension bridge whose
        port is taken, should degrade to Loom's own browser with a visible reason
        rather than leave the desktop unable to start.
        """

        apply = getattr(self.runtime, "browser_set_connection", None)
        if not callable(apply):
            return ""
        raw = settings.get("browser")
        preferences = dict(raw) if isinstance(raw, dict) else {}
        mode = str(preferences.get("mode") or "local-launch").strip()
        engine = str(preferences.get("preferredEngine") or "").strip()
        persist = preferences.get("persistSessions")
        if hasattr(self.runtime, "browser_model_controlled_connection"):
            self.runtime.browser_model_controlled_connection = bool(
                preferences.get("modelSelectsConnection", False)
            )
        private = getattr(self.runtime, "browser_set_private_networks", None)
        if callable(private):
            try:
                private(bool(preferences.get("allowPrivateNetworks", False)))
            except Exception:
                # A live session blocks the change; the stored value still
                # applies the next time the connection is rebuilt.
                pass
        try:
            apply(
                mode,
                cdp_url=str(preferences.get("cdpUrl") or "").strip(),
                persist_profile=None if persist is None else bool(persist),
                engine=engine,
            )
            return ""
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            if mode != "local-launch":
                try:
                    apply("local-launch", engine=engine)
                except Exception:
                    pass
            return reason

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

    def _capability_status(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        if settings is None:
            store = getattr(self, "settings_store", None)
            settings = store.snapshot() if store is not None else {}
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

    @staticmethod
    def _hud_tool_family(tool_name: str) -> str:
        name = str(tool_name or "").strip()
        if name.startswith("computer_"):
            return "computer"
        if name.startswith("browser_"):
            return "browser"
        return ""

    @staticmethod
    def _hud_call_args(event: AgentEvent) -> dict[str, Any]:
        raw = event.data.get("arguments")
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _hud_result_data(event: AgentEvent) -> dict[str, Any]:
        raw = event.data.get("data")
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _hud_float(value: Any, fallback: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        if parsed != parsed:
            return fallback
        return max(0.0, min(1.0, parsed))

    @classmethod
    def _hud_action_from_payload(cls, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        raw = args.get("action")
        if isinstance(raw, dict):
            return dict(raw)
        raw = result.get("action")
        if isinstance(raw, dict):
            return dict(raw)
        execution = result.get("execution")
        if isinstance(execution, dict) and isinstance(execution.get("action"), dict):
            return dict(execution["action"])
        return {}

    @classmethod
    def _hud_point(cls, tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> tuple[float, float] | None:
        name = str(tool_name or "")
        if name.startswith("computer_"):
            action = cls._hud_action_from_payload(args, result)
            point = action.get("point")
            if isinstance(point, dict):
                return cls._hud_float(point.get("x"), 0.52), cls._hud_float(point.get("y"), 0.46)
            end_point = action.get("end_point")
            if isinstance(end_point, dict):
                return cls._hud_float(end_point.get("x"), 0.52), cls._hud_float(end_point.get("y"), 0.46)
            return None
        if not name.startswith("browser_"):
            return None
        raw_index = args.get("index", args.get("target_index", args.get("source_index")))
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = -1
        if index >= 0:
            return (
                max(0.12, min(0.88, 0.22 + ((index * 37) % 55) / 100)),
                max(0.16, min(0.84, 0.25 + ((index * 53) % 48) / 100)),
            )
        if name in {"browser_open", "browser_navigate"}:
            return 0.50, 0.18
        if name in {"browser_state", "browser_refresh", "browser_tabs"}:
            return 0.50, 0.42
        if name == "browser_scroll":
            direction = str(args.get("direction") or "down").casefold()
            return 0.50, 0.28 if direction == "up" else 0.68
        if name in {"browser_back", "browser_switch_tab", "browser_close_tab"}:
            return 0.18, 0.12
        if name == "browser_screenshot":
            return 0.78, 0.18
        return None

    @staticmethod
    def _hud_tool_label(tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
        name = str(tool_name or "")
        action = ReasoningManagedLoomAppServerService._hud_action_from_payload(args, result)
        computer_action = str(action.get("type") or "").replace("_", " ")
        labels = {
            "computer_status": "检查 Computer Use 状态",
            "computer_observe": "观察桌面窗口",
            "computer_action": f"执行桌面动作：{computer_action or 'action'}",
            "computer_step": "执行视觉定位步骤",
            "browser_status": "检查 Browser Use 状态",
            "browser_open": "打开浏览器会话",
            "browser_state": "刷新浏览器状态",
            "browser_navigate": "导航浏览器页面",
            "browser_click": "点击浏览器元素",
            "browser_type": "输入浏览器文本",
            "browser_hover": "悬停浏览器元素",
            "browser_press": "发送浏览器按键",
            "browser_select": "选择下拉选项",
            "browser_drag": "拖拽浏览器元素",
            "browser_scroll": "滚动浏览器页面",
            "browser_back": "浏览器后退",
            "browser_refresh": "刷新页面",
            "browser_tabs": "读取浏览器标签页",
            "browser_switch_tab": "切换浏览器标签页",
            "browser_close_tab": "关闭浏览器标签页",
            "browser_screenshot": "保存浏览器截图",
            "browser_close": "关闭浏览器会话",
        }
        return labels.get(name, name or "执行自动化工具")

    @staticmethod
    def _hud_status_for_event(kind: AgentEventKind, source: str, tool_name: str) -> tuple[int, str, str]:
        label = "Browser Use" if source == "browser" else "Computer Use"
        if kind is AgentEventKind.TOOL_REQUESTED:
            return 1, f"Loom 正在规划 {label}", "模型已选择工具，正在准备执行。"
        if kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
            return 1, f"Loom 等待批准 {label}", "敏感自动化动作正在等待用户批准。"
        if kind is AgentEventKind.TOOL_APPROVED:
            return 2, f"Loom 已批准 {label}", "用户已批准，准备执行自动化动作。"
        if kind is AgentEventKind.TOOL_STARTED:
            return 2, f"Loom 正在执行 {label}", "工具已经开始执行，HUD 仅同步显示当前动作。"
        if kind is AgentEventKind.TOOL_COMPLETED:
            return 4, f"Loom 正在验证 {label}", "动作已完成，正在同步最新状态。"
        if kind is AgentEventKind.TOOL_FAILED:
            return 4, "Loom 自动化失败", "工具执行失败，等待模型根据错误重新规划。"
        if kind is AgentEventKind.TOOL_DENIED:
            return 4, "Loom 自动化被拦截", "动作被权限策略或用户拒绝。"
        return 0, f"Loom 正在运行 {label}", str(tool_name or label)

    def _emit_automation_hud(self, event: AgentEvent) -> None:
        terminal_kinds = {
            AgentEventKind.TURN_COMPLETED,
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }
        if event.kind in terminal_kinds:
            self._notify(
                "hud/update",
                {
                    "threadId": event.session_id,
                    "turnId": event.turn_id,
                    "visible": False,
                    "terminal": True,
                },
            )
            return

        tool_name = str(event.data.get("tool") or "").strip()
        source = self._hud_tool_family(tool_name)
        if not source:
            return
        if event.kind not in {
            AgentEventKind.TOOL_REQUESTED,
            AgentEventKind.TOOL_APPROVAL_REQUIRED,
            AgentEventKind.TOOL_APPROVED,
            AgentEventKind.TOOL_STARTED,
            AgentEventKind.TOOL_COMPLETED,
            AgentEventKind.TOOL_FAILED,
            AgentEventKind.TOOL_DENIED,
        }:
            return

        args = self._hud_call_args(event)
        result = self._hud_result_data(event)
        point = self._hud_point(tool_name, args, result)
        phase, title, thought = self._hud_status_for_event(event.kind, source, tool_name)
        bubble_title = self._hud_tool_label(tool_name, args, result)
        if event.kind is AgentEventKind.TOOL_FAILED:
            error = str(event.data.get("content") or event.data.get("error") or "").strip()
            if error:
                thought = error[:180]

        payload: dict[str, Any] = {
            "threadId": event.session_id,
            "turnId": event.turn_id,
            "callId": str(event.data.get("call_id") or ""),
            "toolName": tool_name,
            "source": source,
            "visible": True,
            "phase": phase,
            "title": title,
            "meta": tool_name,
            "bubbleTitle": bubble_title,
            "thought": thought,
            "confidence": "已定位" if point is not None else "—",
            "actionSource": "browser-use + DOM/CDP" if source == "browser" else "截图 + UIA + Win32 输入",
            "terminal": event.kind in {
                AgentEventKind.TOOL_COMPLETED,
                AgentEventKind.TOOL_FAILED,
                AgentEventKind.TOOL_DENIED,
            },
        }
        if point is not None:
            payload["xNorm"], payload["yNorm"] = point
        if event.kind is AgentEventKind.TOOL_COMPLETED and tool_name not in {
            "computer_status",
            "browser_status",
            "browser_state",
            "browser_tabs",
        }:
            payload["clickRevision"] = event.event_id
        self._notify("hud/update", payload)

    def _on_runtime_event(self, event: AgentEvent) -> None:
        try:
            self._emit_automation_hud(event)
        except Exception:
            pass
        super()._on_runtime_event(event)

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

    def _model_change_blockers(self) -> list[str]:
        with self._guard:
            blockers = set(self._active_sessions)
        for session in self._list_session_objects():
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                blockers.add(session.session_id)
        return sorted(blockers)

    def _install_runtime_platform(self, platform: Any) -> None:
        self.runtime.platform = platform
        setattr(self.runtime, "_provider_streaming_enabled", False)
        enable = getattr(platform, "enable_streaming", None)
        subscribe = getattr(platform, "subscribe_stream", None)
        provider_listener = getattr(self.runtime, "_on_provider_stream", None)
        if callable(enable) and callable(subscribe) and callable(provider_listener):
            enable()
            subscribe(provider_listener)
            setattr(self.runtime, "_provider_streaming_enabled", True)

    def runtime_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        blockers = self._model_change_blockers()
        if blockers:
            raise RuntimeError("finish or stop the current turn before changing model settings")

        provider = str(params.get("provider") or "").strip()
        base_url = str(params.get("baseUrl") or params.get("base_url") or "").strip()
        model = str(params.get("model") or "").strip()
        api_key = str(params.get("apiKey") or params.get("api_key") or "").strip()
        if not provider:
            raise ValueError("provider is required")
        if not model:
            raise ValueError("model is required")
        if not api_key:
            raise ValueError("API key is required")
        reasoning = ReasoningRequest.from_values(
            params.get("reasoningKind") or params.get("reasoning_kind"),
            params.get("reasoningValue") or params.get("reasoning_value"),
        )
        vision = bool(params.get("vision", True))
        timeout = float(params.get("timeout") or 120.0)
        capability = validate_runtime_reasoning(
            model=model,
            provider=provider,
            base_url=base_url,
            reasoning=reasoning,
        )
        platform = build_runtime_model_platform(
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            vision=vision,
            request_timeout_seconds=timeout,
        )

        with self._guard:
            self._install_runtime_platform(platform)
            self.model = model
            self.vision = vision
            setattr(self.runtime, "supports_vision", vision)
            self.runtime.reasoning = reasoning
            self.runtime.reasoning_capability = capability

        updated = self.runtime_status()
        self._notify(
            "runtime/updated",
            {
                "reason": "model_changed",
                "runtime": updated,
            },
        )
        return updated

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
        # Browser preferences are delivered through the same envelope as every
        # other desktop setting, so the changed path is the only signal that the
        # browser connection has to be rebuilt.
        browser_change = _is_browser_setting(capability)
        previous = self.settings_store.snapshot() if browser_change else None
        settings = self.settings_store.set_capability(capability, enabled)
        self._apply_capability_settings(settings)
        browser_error = ""
        if browser_change:
            browser_error = self._apply_browser_settings(settings)
            if browser_error and previous is not None:
                # Keep the stored choice and the live connection in agreement.
                settings = self.settings_store.replace(previous)
                self._apply_browser_settings(settings)
        updated = self.runtime_status()
        self._notify(
            "runtime/updated",
            {
                "reason": "capability_setting_changed",
                "runtime": updated,
            },
        )
        result: dict[str, Any] = {"settings": settings, "runtime": updated}
        if browser_error:
            result["browserWarning"] = browser_error
        return result


class ReasoningManagedLoomRpcController(ManagedStreamingLoomRpcController):
    service: ReasoningManagedLoomAppServerService

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        result["capabilities"]["reasoningControl"] = {
            "read": True,
            "update": True,
            "modelSpecific": True,
        }
        result["capabilities"]["modelSwitch"] = {
            "hot": True,
            "requiresIdleTurn": True,
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
        result["capabilities"]["automationHud"] = {
            "notifications": ["hud/update"],
            "presentationOnly": True,
            "sources": ["browser", "computer"],
        }
        notifications = result["capabilities"].setdefault("notifications", [])
        if "runtime/updated" not in notifications:
            notifications.append("runtime/updated")
        if "hud/update" not in notifications:
            notifications.append("hud/update")
        return result

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "runtime/set_model":
            return self.service.runtime_set_model(params)
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
        vision=bool(vision),
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
