from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, TextIO

from app.ai import ReasoningRequest
from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore, model_id_from_selection
from app.agent_runtime import AgentEvent, AgentEventKind, AgentStatus, PermissionMode
from app.agent_runtime.tools import ToolExposure, ToolRegistry
from app.attachments import MAX_ATTACHMENTS, MAX_FILE_BYTES, MAX_IMAGE_BYTES
from app.runtime_model_switch import build_runtime_model_platform, validate_runtime_reasoning
from app.settings import SETTINGS_UPDATE_PREFIX, LoomSettingsStore
from loom_model_bridge import resolve_model_spec

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


def _setting_path(capability: str) -> str:
    """Read the changed settings path out of the desktop settings-update envelope."""

    raw = str(capability or "").strip()
    if not raw.startswith(SETTINGS_UPDATE_PREFIX):
        return ""
    try:
        payload = json.loads(raw[len(SETTINGS_UPDATE_PREFIX):])
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("path") or "")


def _is_browser_setting(capability: str) -> bool:
    """Detect a browser preference inside the desktop settings-update envelope."""

    return _setting_path(capability).startswith("browser.")


def _is_computer_setting(capability: str) -> bool:
    """Detect a Computer Use preference inside the settings-update envelope."""

    return _setting_path(capability).startswith("computer.")


class ReasoningManagedLoomAppServerService(ManagedStreamingLoomAppServerService):
    """Managed App Server with reasoning, expressions, and desktop settings."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.default_model_selection = str(kwargs.pop("default_model_selection", "") or "").strip()
        self.default_model_provider = str(kwargs.pop("default_model_provider", "") or "").strip()
        self.default_model_base_url = str(kwargs.pop("default_model_base_url", "") or "").strip().rstrip("/")
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
        self._apply_computer_settings(snapshot)

    def _apply_computer_settings(self, settings: dict[str, Any]) -> str:
        """Point the Computer Use operator at the stored screenshot quality.

        Returns an empty string on success, or a reason the stored choice could
        not be honoured. Like the browser path, a preference Loom cannot satisfy
        must not stop the desktop from starting.
        """

        apply = getattr(self.runtime, "computer_set_capture_profile", None)
        if not callable(apply):
            return ""
        raw = settings.get("computer")
        preferences = dict(raw) if isinstance(raw, dict) else {}
        quality = str(preferences.get("screenshotQuality") or "").strip()
        if not quality:
            return ""
        try:
            apply(quality)
            return ""
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

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

    def _hud_point_from_live_frame(
        self,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[float, float] | None:
        """Convert a pending action's window-local point into desktop space.

        A tool-started event knows where the action is about to go, but only in
        the coordinates of the window the screenshot covered. The frame needed to
        convert is the one held by the current observation, so the HUD can show
        the pointer moving to the real target before the click instead of only
        catching up afterwards.
        """

        if not str(tool_name or "").startswith("computer_"):
            return None
        try:
            action = self._hud_action_from_payload(args, result)
            raw = action.get("point")
            if not isinstance(raw, dict):
                raw = action.get("end_point")
            if not isinstance(raw, dict):
                return None

            from app.agent_runtime.computer_single_loop_runtime import desktop_point
            from app.agent_runtime.computer_types import ComputerPoint

            store = getattr(self.runtime, "computer_sessions", None)
            if store is None:
                return None
            frame = store.latest(str(session_id or "")).observation.frame
            converted = desktop_point(
                frame,
                ComputerPoint(float(raw.get("x")), float(raw.get("y"))),
            )
            if converted is None:
                return None
            return float(converted["x_norm"]), float(converted["y_norm"])
        except Exception:
            # The HUD is decoration. Holding the previous position is correct
            # behaviour when the point cannot be placed truthfully.
            return None

    @classmethod
    def _hud_point(cls, tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> tuple[float, float] | None:
        name = str(tool_name or "")
        if name.startswith("computer_"):
            # The overlay spans the whole virtual screen, so it needs a point in
            # that space. An action's own point is normalized against the
            # captured application window instead, and passing it straight
            # through drew the marker at the right fraction of the wrong
            # rectangle -- visibly away from where the pointer actually went.
            screen_point = result.get("screen_point")
            if isinstance(screen_point, dict):
                x_norm = screen_point.get("x_norm")
                y_norm = screen_point.get("y_norm")
                if isinstance(x_norm, (int, float)) and isinstance(y_norm, (int, float)):
                    return cls._hud_float(x_norm, 0.52), cls._hud_float(y_norm, 0.46)
            # No screen_point means this event carries no result yet, which is
            # the case for every tool-started event. The action's own point is
            # available but is normalized against the captured application
            # window, and returning it here would hand a window-local fraction
            # to a full-screen overlay: the cursor jumps somewhere wrong on
            # start and snaps back on completion, which is what users see as the
            # marker flying out and returning. The caller converts it properly
            # using the live frame; None means "no usable point", not "no point".
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
        if point is None:
            point = self._hud_point_from_live_frame(event.session_id, tool_name, args, result)
        phase, title, thought = self._hud_status_for_event(event.kind, source, tool_name)
        bubble_title = self._hud_tool_label(tool_name, args, result)
        single_loop_computer_action = source == "computer" and tool_name == "computer_action"
        if single_loop_computer_action and event.kind is AgentEventKind.TOOL_COMPLETED:
            title = "Loom 正在继续 Computer Use"
            thought = "当前桌面动作已完成，正在检查最新界面并规划下一步。"
        elif single_loop_computer_action and event.kind is AgentEventKind.TOOL_FAILED:
            title = "Loom 正在恢复 Computer Use"
            thought = "当前桌面动作未成功，正在根据最新界面重新规划。"
        elif event.kind is AgentEventKind.TOOL_FAILED:
            error = str(event.data.get("content") or event.data.get("error") or "").strip()
            if error:
                thought = error[:180]

        tool_terminal = (
            event.kind in {
                AgentEventKind.TOOL_COMPLETED,
                AgentEventKind.TOOL_FAILED,
                AgentEventKind.TOOL_DENIED,
            }
            and not single_loop_computer_action
        )

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
            "terminal": tool_terminal,
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

    def _runtime_home(self) -> Path:
        root = Path(getattr(self.store, "root", "")).expanduser().resolve()
        try:
            return root.parents[1]
        except IndexError:
            return root.parent

    @staticmethod
    def _session_reasoning(session: Any) -> ReasoningRequest | None:
        kind = str(getattr(session, "reasoning_kind", "") or "").strip()
        value = str(getattr(session, "reasoning_value", "") or "").strip()
        if not kind or not value:
            return None
        return ReasoningRequest.from_values(kind, value)

    def _ensure_thread_model_metadata(self, session: Any) -> Any:
        if str(getattr(session, "model", "") or "").strip():
            return session
        reasoning = getattr(self.runtime, "reasoning", None)
        session.model_selection = self.default_model_selection
        session.model = self.model
        session.model_provider = self.default_model_provider
        session.model_base_url = self.default_model_base_url
        session.model_vision = bool(self.vision)
        session.reasoning_kind = reasoning.kind.value if reasoning is not None else ""
        session.reasoning_value = reasoning.value if reasoning is not None else ""
        self.store.save(session)
        return session

    def _thread_uses_default_model(self, session: Any) -> bool:
        return (
            str(getattr(session, "model_selection", "") or "") == self.default_model_selection
            and str(getattr(session, "model", "") or "") == self.model
            and str(getattr(session, "model_provider", "") or "") == self.default_model_provider
            and str(getattr(session, "model_base_url", "") or "").rstrip("/") == self.default_model_base_url
        )

    def _ensure_thread_model_runtime(self, session: Any) -> Any:
        session = self._ensure_thread_model_metadata(session)
        has_model = getattr(self.runtime, "has_session_model", None)
        if callable(has_model) and has_model(session.session_id):
            return session

        reasoning = self._session_reasoning(session)
        if self._thread_uses_default_model(session):
            self.runtime.set_session_model(
                session.session_id,
                self.runtime.platform,
                reasoning=reasoning,
            )
            return session

        selection = str(getattr(session, "model_selection", "") or "").strip()
        if not selection:
            raise RuntimeError("thread model selection is missing")
        spec = resolve_model_spec(
            selection,
            model=str(getattr(session, "model", "") or ""),
            home=self._runtime_home(),
        )
        provider = str(spec.get("provider") or "").strip()
        base_url = str(spec.get("baseUrl") or "").strip()
        model = str(spec.get("model") or "").strip()
        api_key = str(spec.get("apiKey") or "").strip()
        vision = bool(spec.get("vision", getattr(session, "model_vision", True)))
        validate_runtime_reasoning(
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
        )
        self.runtime.set_session_model(
            session.session_id,
            platform,
            reasoning=reasoning,
        )
        return session

    def _thread_model_blocked(self, session: Any) -> bool:
        return (
            self._is_active(session.session_id)
            or session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}
        )

    def _thread_runtime_patch(
        self,
        session: Any,
        capability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self.settings_store.snapshot()
        attachments_enabled = settings.get("capabilities", {}).get("attachments", True) is not False
        reasoning = self._session_reasoning(session)
        if capability is None:
            capability = validate_runtime_reasoning(
                model=str(getattr(session, "model", "") or ""),
                provider=str(getattr(session, "model_provider", "") or ""),
                base_url=str(getattr(session, "model_base_url", "") or ""),
                reasoning=reasoning,
            )
        return {
            "threadId": session.session_id,
            "model": str(getattr(session, "model", "") or ""),
            "attachments": {
                "images": bool(getattr(session, "model_vision", True) and attachments_enabled),
                "files": bool(attachments_enabled),
                "maxCount": MAX_ATTACHMENTS,
                "maxImageBytes": MAX_IMAGE_BYTES,
                "maxFileBytes": MAX_FILE_BYTES,
            },
            "reasoning": reasoning.as_safe_dict() if reasoning is not None else None,
            "reasoningCapability": dict(capability) if isinstance(capability, dict) else None,
        }

    def thread_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        session = self._load(session_id)
        if self._thread_model_blocked(session):
            raise RuntimeError("finish or stop this thread's active turn before changing its model")

        requested_selection = str(params.get("selection") or "").strip()
        requested_model = str(params.get("model") or "").strip()
        if not requested_selection:
            raise ValueError("selection is required")
        if not requested_model:
            raise ValueError("model is required")

        # Resolve the connection again inside the App Server and treat that
        # result as authoritative. The renderer used to be able to send
        # selection=builtin:minimax with model=deepseek-flash while retaining
        # MiniMax's base URL/API key. That mixed identity made the UI appear to
        # switch providers even though the actual request still hit MiniMax.
        spec = resolve_model_spec(
            requested_selection,
            model=requested_model,
            home=self._runtime_home(),
        )
        selection = str(spec.get("selection") or requested_selection).strip()
        provider = str(spec.get("provider") or "").strip()
        base_url = str(spec.get("baseUrl") or "").strip().rstrip("/")
        model = str(spec.get("model") or requested_model).strip()
        api_key = str(spec.get("apiKey") or "").strip()
        if not provider:
            raise ValueError("provider is required")
        if not api_key:
            raise ValueError("API key is required")

        resolved_reasoning = spec.get("reasoning")
        if isinstance(resolved_reasoning, dict):
            reasoning = ReasoningRequest.from_values(
                resolved_reasoning.get("kind"),
                resolved_reasoning.get("value"),
            )
        else:
            reasoning = ReasoningRequest.from_values(
                params.get("reasoningKind") or params.get("reasoning_kind"),
                params.get("reasoningValue") or params.get("reasoning_value"),
            )

        vision = bool(params.get("vision", True))
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
            request_timeout_seconds=float(params.get("timeout") or 120.0),
        )

        self.runtime.set_session_model(session_id, platform, reasoning=reasoning)
        session.model_selection = selection
        session.model = model
        session.model_provider = provider
        session.model_base_url = base_url
        session.model_vision = vision
        session.reasoning_kind = reasoning.kind.value if reasoning is not None else ""
        session.reasoning_value = reasoning.value if reasoning is not None else ""
        self.store.save(session)

        record = self._record(session, active=False)
        runtime = self._thread_runtime_patch(session, capability)
        self._notify("thread/updated", {"thread": record, "reason": "model_changed"})
        return {"thread": record, "runtime": runtime}

    def thread_set_reasoning(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        session = self._ensure_thread_model_metadata(self._load(session_id))
        if self._thread_model_blocked(session):
            raise RuntimeError("finish or stop this thread's active turn before changing reasoning")

        reasoning = ReasoningRequest.from_values(params.get("kind"), params.get("value"))
        if reasoning is None:
            raise ValueError("reasoning kind and value are required")
        capability = validate_runtime_reasoning(
            model=session.model,
            provider=session.model_provider,
            base_url=session.model_base_url,
            reasoning=reasoning,
        )
        platform = self.runtime.platform_for_session(session_id)
        self.runtime.set_session_model(session_id, platform, reasoning=reasoning)
        session.reasoning_kind = reasoning.kind.value
        session.reasoning_value = reasoning.value
        self.store.save(session)

        record = self._record(session, active=False)
        runtime = self._thread_runtime_patch(session, capability)
        self._notify("thread/updated", {"thread": record, "reason": "reasoning_changed"})
        return {"thread": record, "runtime": runtime}

    def thread_start(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super().thread_start(params)
        session = self.runtime.get_session(str(result["thread"]["id"]))
        self._ensure_thread_model_metadata(session)
        self.runtime.set_session_model(
            session.session_id,
            self.runtime.platform,
            reasoning=self._session_reasoning(session),
        )
        result["thread"] = self._record(session, active=False)
        return result

    def thread_read(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        self._ensure_thread_model_metadata(self._load(session_id))
        return super().thread_read(params)

    def thread_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        self._ensure_thread_model_metadata(self._load(session_id))
        return super().thread_resume(params)

    def thread_fork(self, params: dict[str, Any]) -> dict[str, Any]:
        source_id = self._required_text(params, "threadId")
        source = self._ensure_thread_model_metadata(self._load(source_id))
        result = super().thread_fork(params)
        fork = self.runtime.get_session(str(result["thread"]["id"]))
        for name in (
            "model_selection",
            "model",
            "model_provider",
            "model_base_url",
            "model_vision",
            "reasoning_kind",
            "reasoning_value",
        ):
            setattr(fork, name, getattr(source, name))
        self.store.save(fork)
        if self._thread_uses_default_model(fork):
            self.runtime.set_session_model(
                fork.session_id,
                self.runtime.platform,
                reasoning=self._session_reasoning(fork),
            )
        result["thread"] = self._record(fork, active=False)
        return result

    def turn_start(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        self._ensure_thread_model_runtime(self._load(session_id))
        return super().turn_start(params)

    def _model_change_blockers(self) -> list[str]:
        with self._guard:
            blockers = set(self._active_sessions)
        for session in self._list_session_objects():
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                blockers.add(session.session_id)
        return sorted(blockers)

    def _install_runtime_platform(self, platform: Any) -> None:
        self.runtime.platform = platform

        # StreamingAgentRuntime owns provider-stream subscription lifecycle. Do
        # not recreate that wiring here: doing so bypasses its de-duplication and
        # can register the same listener twice through transparent platform
        # wrappers after a model switch.
        configure = getattr(self.runtime, "_configure_streaming_platform", None)
        if callable(configure):
            configure(platform)
            return

        # Lightweight test/legacy runtimes may not expose the centralized hook.
        enable = getattr(platform, "enable_streaming", None)
        subscribe = getattr(platform, "subscribe_stream", None)
        provider_listener = getattr(self.runtime, "_on_provider_stream", None)
        if callable(enable) and callable(subscribe) and callable(provider_listener):
            enable()
            subscribe(provider_listener)
            setattr(self.runtime, "_provider_streaming_enabled", True)

    def _persist_model_selection(self, selection: str) -> None:
        value = str(selection or "").strip()
        if not value:
            return
        runtime_home = self.store.root.parents[1]
        model_store = ModelConfigStore(runtime_home)
        model_id = model_id_from_selection(value)
        model_store.set_active(model_id)
        ModelSelectionStore(runtime_home).set(value)

    def _model_runtime_patch(self) -> dict[str, Any]:
        settings = self.settings_store.snapshot()
        attachments_enabled = settings.get("capabilities", {}).get("attachments", True) is not False
        reasoning = getattr(self.runtime, "reasoning", None)
        capability = getattr(self.runtime, "reasoning_capability", None)
        return {
            "model": self.model,
            "attachments": {
                "images": bool(self.vision and attachments_enabled),
                "files": bool(attachments_enabled),
                "maxCount": MAX_ATTACHMENTS,
                "maxImageBytes": MAX_IMAGE_BYTES,
                "maxFileBytes": MAX_FILE_BYTES,
            },
            "reasoning": reasoning.as_safe_dict() if reasoning is not None else None,
            "reasoningCapability": dict(capability) if isinstance(capability, dict) else None,
        }

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
            self.default_model_selection = str(
                params.get("selection") or getattr(self, "default_model_selection", "")
            ).strip()
            self.default_model_provider = provider
            self.default_model_base_url = base_url
            setattr(self.runtime, "supports_vision", vision)
            self.runtime.reasoning = reasoning
            self.runtime.reasoning_capability = capability

        self._persist_model_selection(str(params.get("selection") or ""))
        updated = self._model_runtime_patch()
        self._notify(
            "runtime/updated",
            {
                "reason": "model_changed",
                "runtime": updated,
            },
        )
        return updated

    def runtime_set_reasoning(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._model_change_blockers():
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
        updated = self._model_runtime_patch()
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
        browser_change = _is_browser_setting(capability)
        computer_change = _is_computer_setting(capability)
        previous = self.settings_store.snapshot() if browser_change else None
        settings = self.settings_store.set_capability(capability, enabled)
        self._apply_capability_settings(settings)
        browser_error = ""
        if browser_change:
            browser_error = self._apply_browser_settings(settings)
            if browser_error and previous is not None:
                settings = self.settings_store.replace(previous)
                self._apply_browser_settings(settings)
        computer_error = self._apply_computer_settings(settings) if computer_change else ""
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
        if computer_error:
            result["computerWarning"] = computer_error
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
            "scope": "thread",
            "requiresIdleTurn": True,
            "concurrentThreads": True,
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
        if method == "thread/set_model":
            return self.service.thread_set_model(params)
        if method == "thread/set_reasoning":
            return self.service.thread_set_reasoning(params)
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
