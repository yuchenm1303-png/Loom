from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .computer_types import ComputerAction
from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult

if TYPE_CHECKING:
    from .computer_runtime import ComputerSessionStore, ComputerUseRuntime


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


def computer_tools(runtime: "ComputerUseRuntime") -> tuple[AgentTool, ...]:
    def status(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=True,
            content="Computer Use runtime status.",
            data=runtime.computer_status(context.session_id),
        )

    tools: list[AgentTool] = [
        AgentTool(
            name="computer_status",
            description=(
                "Report Loom Computer Use availability, Windows operator/grounding backend, observation mode, "
                "and security/verification limitations. This does not capture the screen or inject input."
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
        return ToolResult(
            ok=True,
            content=(
                "Computer observation captured. state_revision is mandatory for computer_action; screenshot bytes remain "
                "ephemeral unless save_screenshot was explicitly requested."
            ),
            data=data,
        )

    def act(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
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
        return ToolResult(
            ok=bool(outcome.execution and outcome.execution.ok),
            content="Computer action executed and the desktop was re-observed.",
            data=outcome.to_safe_dict(),
        )

    def step(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
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
        return ToolResult(
            ok=bool(outcome.execution is None or outcome.execution.ok),
            content=content,
            data=outcome.to_safe_dict(),
        )

    sensitive = ToolEffect.SENSITIVE
    tools.extend(
        [
            AgentTool(
                name="computer_observe",
                description=(
                    "Capture the foreground Windows application as a physical-pixel frame, enumerate visible windows and "
                    "UI Automation controls, and return a bounded sanitized observation with a state_revision. "
                    "Use this before deterministic computer_action calls."
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
                    "Stale revisions fail closed."
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
                    "OS action, then re-observe and return verification signals. Loom remains the outer agent loop."
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
