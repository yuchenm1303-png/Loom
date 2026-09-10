from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .computer_types import ComputerAction, ComputerActionType
from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult

if TYPE_CHECKING:
    from .computer_runtime import ComputerSessionStore, ComputerStepOutcome, ComputerUseRuntime


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def _action_schema() -> dict[str, Any]:
    point = _schema(
        {
            "x": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "y": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        ("x", "y"),
    )
    return _schema(
        {
            "type": {
                "type": "string",
                "enum": [
                    "click",
                    "double_click",
                    "right_click",
                    "move",
                    "drag",
                    "scroll",
                    "type",
                    "hotkey",
                    "key",
                    "switch_window",
                    "wait",
                ],
            },
            "point": point,
            "end_point": point,
            "control_id": {"type": "string", "maxLength": 128},
            "window_id": {"type": "string", "maxLength": 128},
            "text": {"type": "string", "maxLength": 20000},
            "keys": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "maxItems": 8,
            },
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 10000},
            "duration_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
        },
        ("type",),
    )


def _store(runtime: "ComputerUseRuntime") -> "ComputerSessionStore":
    store = runtime.computer_sessions
    if store is None:
        raise RuntimeError("Computer Use is unavailable; install Loom with the computer extra on Windows")
    return store


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _diagnostics(runtime: "ComputerUseRuntime") -> Any:
    return getattr(runtime, "computer_diagnostics", None)


def _trace(
    runtime: "ComputerUseRuntime",
    context: ToolContext,
    event: str,
    *,
    operation_id: str,
    tool_name: str,
    started: float | None = None,
    **data: Any,
) -> str:
    diagnostics = _diagnostics(runtime)
    trace = getattr(diagnostics, "trace", None)
    if not callable(trace):
        return ""
    payload = {
        "tool_name": tool_name,
        "permission_mode": context.permission_mode,
        "workspace": str(context.workspace),
        **data,
    }
    if started is not None:
        payload["duration_ms"] = _elapsed_ms(started)
    try:
        return str(trace(context.session_id, context.turn_id, event, operation_id=operation_id, **payload))
    except Exception:
        return ""


def _trace_meta(runtime: "ComputerUseRuntime", context: ToolContext) -> dict[str, object]:
    diagnostics = _diagnostics(runtime)
    trace_path = getattr(diagnostics, "trace_path", None)
    status = getattr(diagnostics, "status", None)
    payload: dict[str, object] = {}
    if callable(trace_path):
        try:
            payload["trace_path"] = str(trace_path(context.session_id, context.turn_id))
        except Exception:
            pass
    if callable(status):
        try:
            payload["diagnostics"] = status()
        except Exception:
            pass
    return payload


def _safe_arguments(runtime: "ComputerUseRuntime", arguments: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _diagnostics(runtime)
    raw_allowed = bool(getattr(diagnostics, "raw", False))
    copied = dict(arguments)
    action = copied.get("action")
    if isinstance(action, dict):
        action_copy = dict(action)
        if "text" in action_copy:
            text = str(action_copy.get("text") or "")
            action_copy["text"] = text if raw_allowed else "[TRANSIENT_TEXT]"
            action_copy["text_length"] = len(text)
        copied["action"] = action_copy
    if "instruction" in copied:
        instruction = str(copied.get("instruction") or "")
        copied["instruction"] = instruction if raw_allowed else {"length": len(instruction)}
    return copied


def _active_window_summary(value: Any) -> dict[str, object] | None:
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        payload = dict(to_dict())
    except Exception:
        return None
    for key in ("title", "process_name"):
        if key in payload:
            payload[key] = str(payload.get(key) or "")[:500]
    return payload


def _observation_summary(snapshot: Any) -> dict[str, object]:
    observation = getattr(snapshot, "observation", None)
    frame = getattr(observation, "frame", None)
    return {
        "state_revision": int(getattr(snapshot, "state_revision", 0) or 0),
        "observation_id": str(getattr(observation, "observation_id", "") or ""),
        "image_sha256": str(getattr(observation, "image_sha256", "") or ""),
        "image_bytes": len(bytes(getattr(observation, "image_png", b"") or b"")),
        "frame": frame.to_dict() if hasattr(frame, "to_dict") else None,
        "active_window": _active_window_summary(getattr(observation, "active_window", None)),
        "windows_total": len(tuple(getattr(observation, "windows", ()) or ())),
        "controls_total": len(tuple(getattr(observation, "controls", ()) or ())),
    }


def _point_geometry(outcome: "ComputerStepOutcome") -> dict[str, object]:
    action = outcome.prediction.action
    frame = outcome.before.observation.frame
    payload: dict[str, object] = {}
    if action.point is not None:
        screen_x, screen_y = frame.to_screen(action.point)
        payload["point"] = action.point.to_dict()
        payload["screen_point"] = {"x": screen_x, "y": screen_y}
    if action.end_point is not None:
        screen_x, screen_y = frame.to_screen(action.end_point)
        payload["end_point"] = action.end_point.to_dict()
        payload["screen_end_point"] = {"x": screen_x, "y": screen_y}
    return payload


def _outcome_summary(outcome: "ComputerStepOutcome") -> dict[str, object]:
    execution = outcome.execution.to_safe_dict() if outcome.execution is not None else None
    return {
        "before": _observation_summary(outcome.before),
        "after": _observation_summary(outcome.after) if outcome.after is not None else None,
        "action": outcome.prediction.action.safe_dict(),
        "terminal": outcome.prediction.action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER},
        "execution": execution,
        "verification": dict(outcome.verification),
        "geometry": _point_geometry(outcome),
    }


def _enrich_outcome_payload(
    runtime: "ComputerUseRuntime",
    context: ToolContext,
    outcome: "ComputerStepOutcome",
) -> dict[str, object]:
    payload = outcome.to_safe_dict()
    payload["geometry"] = _point_geometry(outcome)
    trace_meta = _trace_meta(runtime, context)
    if trace_meta:
        payload["trace"] = trace_meta
    return payload


def computer_tools(runtime: "ComputerUseRuntime") -> tuple[AgentTool, ...]:
    def status(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        operation_id = _diagnostics(runtime).operation_id() if _diagnostics(runtime) is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        data = runtime.computer_status(context.session_id)
        data["trace"] = _trace_meta(runtime, context)
        _trace(
            runtime,
            context,
            "status.read",
            operation_id=operation_id,
            tool_name="computer_status",
            started=started,
            status="completed",
            result=data,
        )
        return ToolResult(
            ok=True,
            content="Computer Use runtime status.",
            data=data,
        )

    tools: list[AgentTool] = [
        AgentTool(
            name="computer_status",
            description=(
                "Report Loom Computer Use availability, Windows operator/grounding backend, observation mode, "
                "diagnostic log paths, and security/verification limitations. This does not capture the screen or inject input."
            ),
            input_schema=_schema({}),
            handler=status,
            effect=ToolEffect.READ_ONLY,
        )
    ]
    if runtime.computer_sessions is None:
        return tuple(tools)

    def observe(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        _trace(
            runtime,
            context,
            "observe.started",
            operation_id=operation_id,
            tool_name="computer_observe",
            status="started",
            arguments=_safe_arguments(runtime, arguments),
        )
        try:
            snapshot = _store(runtime).observe(context.session_id)
            data = snapshot.to_safe_dict(control_limit=int(arguments.get("control_limit", 80)))
            if bool(arguments.get("save_screenshot", False)):
                raw_path = str(arguments.get("path") or "").strip()
                if raw_path:
                    relative = Path(raw_path)
                    if relative.suffix.casefold() != ".png":
                        raise ValueError("computer_observe screenshot path must end in .png")
                else:
                    relative = Path("computer-screenshots") / f"{uuid.uuid4().hex[:16]}.png"
                target = context.resolve_workspace_path(relative.as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(snapshot.observation.image_png)
                data["screenshot_path"] = relative.as_posix()
            data["trace"] = _trace_meta(runtime, context)
            _trace(
                runtime,
                context,
                "observe.completed",
                operation_id=operation_id,
                tool_name="computer_observe",
                started=started,
                status="completed",
                observation=_observation_summary(snapshot),
                result={
                    "state_revision": snapshot.state_revision,
                    "observation_id": snapshot.observation.observation_id,
                    "image_sha256": snapshot.observation.image_sha256,
                    "screenshot_path": data.get("screenshot_path", ""),
                },
            )
            return ToolResult(
                ok=True,
                content=(
                    "Computer observation captured. state_revision is mandatory for computer_action; screenshot bytes remain "
                    "ephemeral unless save_screenshot was explicitly requested."
                ),
                data=data,
            )
        except Exception as exc:
            _trace(
                runtime,
                context,
                "observe.failed",
                operation_id=operation_id,
                tool_name="computer_observe",
                started=started,
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    def act(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        _trace(
            runtime,
            context,
            "action.started",
            operation_id=operation_id,
            tool_name="computer_action",
            status="started",
            arguments=_safe_arguments(runtime, arguments),
        )
        try:
            raw_action = arguments.get("action")
            if not isinstance(raw_action, dict):
                raise ValueError("computer_action action must be an object")
            action_payload = dict(raw_action)
            if str(action_payload.get("type") or "") == "type" and "text" in action_payload:
                action_payload["text"] = runtime.consume_computer_transient(str(action_payload.get("text") or ""))
            action = ComputerAction.from_dict(action_payload)
            outcome = _store(runtime).execute(
                context.session_id,
                int(arguments["state_revision"]),
                action,
            )
            data = _enrich_outcome_payload(runtime, context, outcome)
            _trace(
                runtime,
                context,
                "action.completed",
                operation_id=operation_id,
                tool_name="computer_action",
                started=started,
                status="completed" if bool(outcome.execution and outcome.execution.ok) else "failed",
                outcome=_outcome_summary(outcome),
            )
            return ToolResult(
                ok=bool(outcome.execution and outcome.execution.ok),
                content="Computer action executed and the desktop was re-observed.",
                data=data,
            )
        except Exception as exc:
            _trace(
                runtime,
                context,
                "action.failed",
                operation_id=operation_id,
                tool_name="computer_action",
                started=started,
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    def step(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        _trace(
            runtime,
            context,
            "step.started",
            operation_id=operation_id,
            tool_name="computer_step",
            status="started",
            grounder=getattr(runtime, "computer_grounder_name", ""),
            arguments=_safe_arguments(runtime, arguments),
        )
        try:
            instruction = runtime.consume_computer_transient(str(arguments["instruction"]))
            if len(instruction) > 20_000:
                raise ValueError("computer_step instruction exceeds 20,000 characters")
            outcome = _store(runtime).step(context.session_id, instruction)
            terminal = outcome.prediction.action.type.value
            if terminal == "call_user":
                content = "Computer visual policy requested user assistance."
            elif terminal == "finish":
                content = "Computer visual policy considers the current GUI instruction complete."
            else:
                content = "Computer visual policy executed one action and the desktop was re-observed."
            data = _enrich_outcome_payload(runtime, context, outcome)
            _trace(
                runtime,
                context,
                "step.completed",
                operation_id=operation_id,
                tool_name="computer_step",
                started=started,
                status="completed" if bool(outcome.execution is None or outcome.execution.ok) else "failed",
                grounder=getattr(runtime, "computer_grounder_name", ""),
                outcome=_outcome_summary(outcome),
                prediction={
                    "action": outcome.prediction.action.safe_dict(),
                    "thought": outcome.prediction.thought if bool(getattr(diagnostics, "raw", False)) else {"length": len(outcome.prediction.thought)},
                },
            )
            return ToolResult(
                ok=bool(outcome.execution is None or outcome.execution.ok),
                content=content,
                data=data,
            )
        except Exception as exc:
            _trace(
                runtime,
                context,
                "step.failed",
                operation_id=operation_id,
                tool_name="computer_step",
                started=started,
                status="failed",
                grounder=getattr(runtime, "computer_grounder_name", ""),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    sensitive = ToolEffect.SENSITIVE
    tools.extend(
        [
            AgentTool(
                name="computer_observe",
                description=(
                    "Capture the foreground Windows application as a physical-pixel frame, enumerate visible windows and "
                    "UI Automation controls, write a per-turn Computer Use trace, and return a bounded sanitized observation "
                    "with a state_revision. Use this before deterministic computer_action calls."
                ),
                input_schema=_schema(
                    {
                        "control_limit": {"type": "integer", "minimum": 0, "maximum": 200},
                        "save_screenshot": {"type": "boolean"},
                        "path": {"type": "string", "maxLength": 1000},
                    }
                ),
                handler=observe,
                effect=sensitive,
            ),
            AgentTool(
                name="computer_action",
                description=(
                    "Execute exactly one typed Windows GUI action against the latest computer_observe state_revision. "
                    "Prefer control_id for UIA-native clicks/edits; normalized point coordinates are frame-local 0..1 fallbacks. "
                    "Stale revisions fail closed. The result includes replay trace metadata and resolved screen coordinates when available."
                ),
                input_schema=_schema(
                    {
                        "state_revision": {"type": "integer", "minimum": 1},
                        "action": _action_schema(),
                    },
                    ("state_revision", "action"),
                ),
                handler=act,
                effect=sensitive,
            ),
        ]
    )
    if runtime.computer_sessions.grounder is not None:
        tools.append(
            AgentTool(
                name="computer_step",
                description=(
                    "Perform exactly one screenshot-driven GUI policy step: capture screenshot + UIA context, ask the configured "
                    "visual grounding backend (UI-TARS adapter by default when configured) for one next action, execute at most one "
                    "OS action, then re-observe and return verification signals. Loom remains the outer agent loop, and the step is "
                    "written to the per-turn Computer Use trace for replay/debugging."
                ),
                input_schema=_schema(
                    {"instruction": {"type": "string", "minLength": 1, "maxLength": 20000}},
                    ("instruction",),
                ),
                handler=step,
                effect=sensitive,
            )
        )
    return tuple(tools)


__all__ = ["computer_tools"]
