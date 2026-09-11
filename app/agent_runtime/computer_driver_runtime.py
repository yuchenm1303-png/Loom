from __future__ import annotations

from collections import deque
from dataclasses import replace
import os
from typing import Any

from .computer_driver import ComputerDriverEvent, ComputerTaskDriver
from .computer_runtime import ComputerUseRuntime
from .computer_ufo_driver import UfoWindowsDriver
from .contracts import AgentEventKind, ToolEffect
from .tools import AgentTool, ToolContext, ToolExposure, ToolRegistry, ToolResult


_DRIVER_MODES = {"auto", "ufo", "legacy"}
_LOW_LEVEL_COMPUTER_TOOLS = {
    "computer_observe",
    "computer_action",
    "computer_step",
}
_SENSITIVE_DRIVER_KEYS = {
    "content",
    "error",
    "instruction",
    "keys",
    "message",
    "preview",
    "prompt",
    "raw",
    "request",
    "response",
    "stderr_tail",
    "task",
    "text",
    "traceback",
}


def _driver_mode() -> str:
    value = str(os.environ.get("LOOM_COMPUTER_DRIVER") or "auto").strip().casefold()
    return value if value in _DRIVER_MODES else "auto"


def _ufo_base_url(provider: str, value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base.casefold().endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    if not base and provider == "openai":
        return "https://api.openai.com/v1"
    return base


def _safe_driver_data(value: Any, *, key: str = "") -> Any:
    """Keep provider events useful without persisting task/input/error payloads."""

    key_folded = str(key or "").casefold()
    if key_folded in _SENSITIVE_DRIVER_KEYS:
        if value in (None, "", [], {}):
            return value
        return "[REDACTED_DRIVER_DATA]"
    if isinstance(value, dict):
        return {
            str(name): _safe_driver_data(item, key=str(name))
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_driver_data(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


class ComputerDriverRuntime(ComputerUseRuntime):
    """Computer Use runtime that delegates full desktop tasks to a mature driver.

    When the mature driver layer is active, normal model-facing desktop work has one
    canonical entrypoint: ``computer_run_task``. The historical observe/action/step
    tools stay installed as deferred diagnostics/fallback primitives instead of
    competing with UFO for every model decision. Explicit ``legacy`` mode preserves
    the historical direct exposure unchanged.
    """

    def __init__(
        self,
        *args: Any,
        computer_driver: ComputerTaskDriver | None = None,
        computer_driver_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        mode = str(computer_driver_mode or _driver_mode()).strip().casefold()
        self.computer_driver_mode = mode if mode in _DRIVER_MODES else "auto"
        self.computer_driver: ComputerTaskDriver | None = computer_driver
        if (
            self.computer_driver is None
            and os.name == "nt"
            and self.computer_driver_mode != "legacy"
        ):
            self.computer_driver = UfoWindowsDriver.from_environment(
                strict=self.computer_driver_mode == "ufo"
            )

        self._legacy_computer_run_task = self.tools.get("computer_run_task")
        self._install_driver_task_tool()
        self._sync_driver_model_from_platform()

    def _sync_driver_model_from_platform(self) -> None:
        """Reuse the current Loom vision connection in the isolated UFO process."""

        driver = self.computer_driver
        if not isinstance(driver, UfoWindowsDriver):
            return
        platform = getattr(self, "platform", None)
        metadata = None
        for _ in range(3):
            candidate = getattr(platform, "_loom_model_connection", None)
            if isinstance(candidate, dict):
                metadata = candidate
                break
            platform = getattr(platform, "_delegate", None)
            if platform is None:
                break
        if not isinstance(metadata, dict) or not bool(metadata.get("vision", True)):
            return
        provider = str(metadata.get("provider") or "").strip().casefold()
        if provider not in {"openai", "openai_compatible"}:
            return
        api_key = str(metadata.get("api_key") or "").strip()
        api_model = str(metadata.get("model") or "").strip()
        if not api_key or not api_model:
            return
        api_base = _ufo_base_url(provider, str(metadata.get("base_url") or ""))
        updated = replace(
            driver.config,
            api_type="openai",
            api_base=api_base,
            api_key=api_key,
            api_model=api_model,
        )
        if updated == driver.config:
            return
        # Model switches are only allowed while Loom has no active turn. Closing an
        # idle sidecar prevents cached UFO LLM services from retaining old routing.
        driver.close()
        driver.config = updated

    def _install_driver_task_tool(self) -> None:
        if self.computer_driver_mode == "legacy" or self.computer_driver is None:
            return

        replacement = AgentTool(
            name="computer_run_task",
            description=(
                "Run a normal multi-step Windows desktop task through Loom's mature "
                "Computer Driver. On Windows the preferred engine is Microsoft UFO²: "
                "it selects and anchors the target application, keeps UIA actions "
                "separate from visual coordinate actions, and owns the continuous GUI "
                "agent loop. Prefer this tool for desktop work. Low-level "
                "computer_observe/action/step are deferred diagnostics only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 20000,
                        "description": "High-level desktop task to complete.",
                    },
                    "stop_when": {
                        "type": "string",
                        "maxLength": 4000,
                        "description": (
                            "Optional explicit stop condition. Do not include secrets "
                            "that are not required by the task."
                        ),
                    },
                    "max_steps": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": (
                            "Optional task step ceiling forwarded to the selected driver."
                        ),
                    },
                },
                "required": ["task"],
                "additionalProperties": False,
            },
            handler=self._handle_driver_task,
            effect=ToolEffect.SENSITIVE,
        )

        rebuilt: list[AgentTool] = []
        replaced_run_task = False
        for tool in self.tools.all():
            if tool.name == "computer_run_task":
                rebuilt.append(replacement)
                replaced_run_task = True
                continue
            if tool.name in _LOW_LEVEL_COMPUTER_TOOLS:
                rebuilt.append(replace(tool, exposure=ToolExposure.DEFERRED))
                continue
            rebuilt.append(tool)
        if not replaced_run_task:
            rebuilt.append(replacement)
        self.tools = ToolRegistry(tuple(rebuilt))

    def _driver_ready(self) -> bool:
        self._sync_driver_model_from_platform()
        driver = self.computer_driver
        if driver is None:
            return False
        try:
            status = dict(driver.status())
        except Exception:
            return False
        return bool(status.get("ready"))

    def _trace_driver_event(
        self,
        context: ToolContext,
        event: ComputerDriverEvent,
    ) -> str:
        diagnostics = getattr(self, "computer_diagnostics", None)
        if diagnostics is None:
            return ""
        try:
            return diagnostics.trace(
                context.session_id,
                context.turn_id,
                f"driver.{event.kind}",
                operation_id=event.task_id,
                driver=str(
                    getattr(self.computer_driver, "name", "computer-driver")
                ),
                driver_event=event.to_safe_dict(),
            )
        except Exception:
            return ""

    @staticmethod
    def _emit_action_event(
        context: ToolContext,
        event: ComputerDriverEvent,
        pending: list[str | None],
    ) -> None:
        data = dict(event.data)
        kind = event.kind
        if kind == "action.started":
            call_id = f"driver:{event.task_id}:{event.sequence}"
            pending[0] = call_id
            action_name = str(data.get("action") or "action")
            hud = (
                data.get("hud_point")
                if isinstance(data.get("hud_point"), dict)
                else {}
            )
            action: dict[str, Any] = {"type": action_name}
            if hud:
                action["point"] = {
                    "x": float(hud.get("x_norm") or 0.0),
                    "y": float(hud.get("y_norm") or 0.0),
                }
            arguments = {
                "action": action,
                "driver": "ufo2-sidecar",
                "parameters": dict(data.get("parameters") or {}),
                "window": dict(data.get("window") or {}),
            }
            base = {
                "call_id": call_id,
                "tool": "computer_action",
                "arguments": arguments,
                "nested": True,
                "parent_call_id": event.task_id,
                "driver": "ufo2-sidecar",
                "driver_progress": True,
            }
            context.emit(AgentEventKind.TOOL_REQUESTED, base)
            context.emit(AgentEventKind.TOOL_STARTED, base)
            return

        if kind == "action.completed" and pending[0]:
            call_id = pending[0]
            pending[0] = None
            result = dict(data.get("result") or {})
            ok = bool(result.get("ok"))
            # Close the nested transcript/Inspector lifecycle by call id while using
            # a non-computer terminal identity. HUD subscribes to computer_* only,
            # so individual UFO actions cannot hide the task-level HUD.
            context.emit(
                AgentEventKind.TOOL_COMPLETED if ok else AgentEventKind.TOOL_FAILED,
                {
                    "call_id": call_id,
                    "tool": "driver_action_result",
                    "nested": True,
                    "parent_call_id": event.task_id,
                    "driver": "ufo2-sidecar",
                    "driver_progress": True,
                    "ok": ok,
                    "content": (
                        "driver action completed" if ok else "driver action failed"
                    ),
                    "data": {
                        "driver": "ufo2-sidecar",
                        "action_name": str(data.get("action") or "action"),
                        "window": dict(data.get("window") or {}),
                    },
                },
            )

    def _handle_driver_task(
        self,
        context: ToolContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        self._sync_driver_model_from_platform()
        driver = self.computer_driver
        driver_status = (
            dict(driver.status())
            if driver is not None
            else {"ready": False, "reason": "driver is disabled"}
        )

        if driver is None or not bool(driver_status.get("ready")):
            if (
                self.computer_driver_mode == "auto"
                and self._legacy_computer_run_task is not None
            ):
                # Auto mode has one model-facing task tool even before UFO is
                # installed. Its handler can temporarily use the historical runner,
                # so the outer model never needs to learn or choose the legacy
                # observe/action/step plumbing.
                return self._legacy_computer_run_task.handler(context, arguments)
            reason = str(
                driver_status.get("reason") or "Computer Driver is not ready"
            )
            return ToolResult(
                False,
                reason,
                {
                    "driver": driver_status,
                    "mode": self.computer_driver_mode,
                    "setup": (
                        "Run `cd desktop-react && npm run setup:ufo`, then restart Loom."
                    ),
                },
            )

        task = self.consume_computer_transient(str(arguments.get("task") or ""))
        stop_when = self.consume_computer_transient(
            str(arguments.get("stop_when") or "")
        )
        max_steps_raw = arguments.get("max_steps")
        max_steps = int(max_steps_raw) if max_steps_raw is not None else None
        recent_events: deque[dict[str, Any]] = deque(maxlen=40)
        trace_file = ""
        pending_action: list[str | None] = [None]

        def on_event(event: ComputerDriverEvent) -> None:
            nonlocal trace_file
            safe_event = ComputerDriverEvent(
                task_id=event.task_id,
                sequence=event.sequence,
                kind=event.kind,
                data=_safe_driver_data(dict(event.data)),
                created_at=event.created_at,
            )
            recent_events.append(safe_event.to_safe_dict())
            path = self._trace_driver_event(context, safe_event)
            if path:
                trace_file = path
            self._emit_action_event(context, safe_event, pending_action)

        try:
            result = driver.run_task(
                task,
                stop_when=stop_when,
                max_steps=max_steps,
                on_event=on_event,
                is_cancelled=context.is_cancelled,
            )
        except Exception as exc:
            return ToolResult(
                False,
                (
                    "Computer Driver failed before returning a task result: "
                    f"{type(exc).__name__}"
                ),
                {
                    "driver": dict(driver.status()),
                    "mode": self.computer_driver_mode,
                    "trace_file": trace_file,
                    "recent_events": list(recent_events),
                },
            )

        payload = {
            "task_id": result.task_id,
            "status": result.status,
            "ok": bool(result.ok),
            "summary": result.summary,
            "data": _safe_driver_data(dict(result.data)),
            "driver": dict(driver.status()),
            "driver_name": str(
                getattr(driver, "name", "computer-driver")
            ),
            "mode": self.computer_driver_mode,
            "trace_file": trace_file,
            "recent_events": list(recent_events),
        }
        content = result.summary or (
            "Computer Driver completed the desktop task."
            if result.ok
            else f"Computer Driver ended with status {result.status}."
        )
        return ToolResult(bool(result.ok), content, payload)

    def computer_status(
        self,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self._sync_driver_model_from_platform()
        status = dict(super().computer_status(session_id))
        driver = self.computer_driver
        driver_status: dict[str, Any]
        if driver is None:
            driver_status = {
                "name": "legacy",
                "ready": False,
                "reason": "mature driver disabled",
            }
        else:
            try:
                driver_status = dict(driver.status())
            except Exception as exc:
                driver_status = {
                    "name": getattr(driver, "name", "computer-driver"),
                    "ready": False,
                    "reason": str(exc),
                }
        status["driver_mode"] = self.computer_driver_mode
        status["task_driver"] = driver_status
        if bool(driver_status.get("ready")):
            task_runner = str(driver_status.get("name") or "computer-driver")
        elif (
            self._legacy_computer_run_task is not None
            and self.computer_driver_mode != "ufo"
        ):
            task_runner = "legacy-loom"
        else:
            task_runner = "unavailable"
        status["task_runner"] = task_runner
        status["legacy_task_runner_available"] = (
            self._legacy_computer_run_task is not None
        )
        status["low_level_tools"] = (
            "direct"
            if self.computer_driver_mode == "legacy" or self.computer_driver is None
            else "deferred"
        )
        if self.computer_driver_mode == "ufo":
            status["enabled"] = bool(driver_status.get("ready"))
        return status

    def pause_computer_driver(self) -> bool:
        return bool(self.computer_driver and self.computer_driver.pause())

    def resume_computer_driver(self) -> bool:
        return bool(self.computer_driver and self.computer_driver.resume())

    def cancel_computer_driver(self, *, reason: str = "user_requested") -> bool:
        return bool(
            self.computer_driver
            and self.computer_driver.cancel(reason=reason)
        )

    def close(self) -> None:
        driver = self.computer_driver
        self.computer_driver = None
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
        super().close()


__all__ = ["ComputerDriverRuntime"]
