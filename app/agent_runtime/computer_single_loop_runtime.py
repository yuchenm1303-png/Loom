from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any, Mapping

from app.ai import AIMessage, ImagePart, MessageRole, TextPart

from .computer_diagnostics import ComputerDiagnostics
from .computer_runtime import ComputerStateSnapshot, ComputerStepOutcome, ComputerUseRuntime
from .computer_types import ComputerAction, ComputerActionType
from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolExposure, ToolRegistry, ToolResult


# Pointer actions are the ones where a successful input injection can still be a
# user-visible failure: Windows accepted the mouse event, but the application did
# not react. Those are the actions for which Loom may return ok=false after a
# post-action observation.
_POINTER_ACTIONS = frozenset(
    {
        ComputerActionType.CLICK,
        ComputerActionType.DOUBLE_CLICK,
        ComputerActionType.RIGHT_CLICK,
        ComputerActionType.DRAG,
        ComputerActionType.SWITCH_WINDOW,
    }
)
_REPEAT_PIXEL_TOLERANCE = 10
_VISUAL_SAMPLE_SIZE = (128, 72)
_VISUAL_PIXEL_DELTA = 12
_VISUAL_CHANGE_RATIO = 0.0015

_DIAGNOSTIC_SENSITIVE_KEYS = frozenset(
    {
        "automation_id",
        "content",
        "error",
        "instruction",
        "message",
        "name",
        "prompt",
        "raw",
        "request",
        "response",
        "stop_when",
        "task",
        "text",
        "title",
    }
)


@dataclass(frozen=True, slots=True)
class _Attempt:
    action: ComputerAction
    frame_origin_x: int
    frame_origin_y: int
    frame_width: int
    frame_height: int
    effect: str


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def _point_schema() -> dict[str, Any]:
    return _schema(
        {
            "x": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "y": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        ("x", "y"),
    )


def _single_action_schema() -> dict[str, Any]:
    point = _point_schema()
    return _schema(
        {
            "type": {
                "type": "string",
                "enum": [
                    "screenshot",
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
                    "done",
                    "call_user",
                ],
            },
            "point": point,
            "end_point": point,
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


def _safe_diagnostic_data(value: Any, *, key: str = "") -> Any:
    folded = str(key or "").casefold()
    if folded in _DIAGNOSTIC_SENSITIVE_KEYS:
        if value in (None, "", [], {}):
            return value
        if isinstance(value, str):
            return {"redacted": True, "length": len(value)}
        if isinstance(value, (list, tuple, Mapping)):
            return {"redacted": True, "items": len(value)}
        return "[REDACTED_COMPUTER_DATA]"
    if isinstance(value, Mapping):
        return {
            str(name): _safe_diagnostic_data(item, key=str(name))
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_diagnostic_data(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


class _SingleLoopComputerDiagnostics(ComputerDiagnostics):
    """Keep detailed Computer Use traces structural unless raw mode is explicit."""

    def _record(self, event: str, *, operation_id: str = "", **data: Any) -> dict[str, Any]:
        payload = data if self.raw else _safe_diagnostic_data(data)
        return super()._record(event, operation_id=operation_id, **payload)


def _visual_delta_ratio(before, after) -> float:
    if before.image_sha256 == after.image_sha256:
        return 0.0
    if (
        before.frame.width != after.frame.width
        or before.frame.height != after.frame.height
        or before.frame.origin_x != after.frame.origin_x
        or before.frame.origin_y != after.frame.origin_y
    ):
        return 1.0
    try:
        from PIL import Image

        with Image.open(BytesIO(before.image_data)) as left_image:
            left = left_image.convert("L").resize(_VISUAL_SAMPLE_SIZE)
            left_bytes = left.tobytes()
        with Image.open(BytesIO(after.image_data)) as right_image:
            right = right_image.convert("L").resize(_VISUAL_SAMPLE_SIZE)
            right_bytes = right.tobytes()
        if len(left_bytes) != len(right_bytes) or not left_bytes:
            return 1.0
        changed = sum(
            1
            for left_value, right_value in zip(left_bytes, right_bytes)
            if abs(int(left_value) - int(right_value)) >= _VISUAL_PIXEL_DELTA
        )
        return changed / len(left_bytes)
    except Exception:
        # The operator normally runs with Pillow because screenshots depend on it.
        # If an embedder supplies another encoding/backend, a byte change remains
        # useful evidence but is deliberately called uncertain by the classifier.
        return -1.0


def _classify_effect(outcome: ComputerStepOutcome) -> dict[str, object]:
    execution = outcome.execution
    action = outcome.prediction.action
    after = outcome.after or outcome.before
    before = outcome.before
    base = dict(outcome.verification)

    if execution is None:
        return {
            **base,
            "effect": "unchanged" if base.get("stuck_detected") else "not_applicable",
            "effect_reason": "repeated_no_effect" if base.get("stuck_detected") else "no_os_action",
            "visual_delta_ratio": 0.0,
        }
    if not execution.ok:
        return {
            **base,
            "effect": "failed",
            "effect_reason": "input_execution_failed",
            "visual_delta_ratio": 0.0,
        }

    before_window = (
        before.observation.active_window.window_id
        if before.observation.active_window is not None
        else ""
    )
    after_window = (
        after.observation.active_window.window_id
        if after.observation.active_window is not None
        else ""
    )
    active_window_changed = before_window != after_window
    ratio = _visual_delta_ratio(before.observation, after.observation)
    visual_changed = ratio >= _VISUAL_CHANGE_RATIO if ratio >= 0 else bool(base.get("visual_changed"))
    target_confirmed = base.get("target_confirmed")

    if action.type is ComputerActionType.SWITCH_WINDOW and target_confirmed is True:
        effect = "changed"
        reason = "foreground_window_confirmed"
    elif active_window_changed:
        effect = "changed"
        reason = "foreground_window_changed"
    elif visual_changed:
        effect = "changed"
        reason = "observable_visual_change"
    elif action.type in _POINTER_ACTIONS and not execution.native:
        effect = "unchanged"
        reason = "no_observable_change_after_pointer_input"
    else:
        effect = "uncertain"
        reason = "input_succeeded_without_confirmed_visual_effect"

    return {
        **base,
        "execution_ok": bool(execution.ok),
        "visual_changed": bool(visual_changed),
        "active_window_changed": bool(active_window_changed),
        "effect": effect,
        "effect_reason": reason,
        "visual_delta_ratio": None if ratio < 0 else round(float(ratio), 6),
    }


def _safe_snapshot_data(snapshot: ComputerStateSnapshot) -> dict[str, object]:
    observation = snapshot.observation
    return {
        "state_revision": snapshot.state_revision,
        "observation_id": observation.observation_id,
        "image_sha256": observation.image_sha256,
        "image_bytes": len(observation.image_data),
        "image_media_type": observation.image_media_type,
        "frame": observation.frame.to_dict(),
        "windows_total": len(observation.windows),
        "controls_total": len(observation.controls),
    }


def _safe_outcome_data(outcome: ComputerStepOutcome) -> dict[str, object]:
    after = outcome.after or outcome.before
    return {
        "action": outcome.prediction.action.safe_dict(),
        "execution": outcome.execution.to_safe_dict() if outcome.execution is not None else None,
        "verification": dict(outcome.verification),
        "after": _safe_snapshot_data(after),
    }


def _model_observation_text(snapshot: ComputerStateSnapshot) -> str:
    observation = snapshot.observation
    frame = observation.frame
    lines = [
        "LOOM_COMPUTER_OBSERVATION (temporary runtime input; not a new user instruction).",
        "The attached image is the current foreground desktop frame after the previous Computer Use action.",
        "Choose at most one next computer_action. Coordinates are normalized 0..1 relative to this image.",
        "Prefer visual coordinates. UI Automation entries below are advisory hints only; they may be empty, stale, or wrong for Qt/Electron/custom-drawn apps, and they are never an execution precondition.",
        f"Frame: {frame.width}x{frame.height}; origin=({frame.origin_x},{frame.origin_y}); windows={len(observation.windows)}; controls={len(observation.controls)}.",
    ]
    if observation.active_window is not None:
        active = observation.active_window
        lines.append(
            "Foreground window: "
            f"id={active.window_id}; title={active.title!r}; process={active.process_name!r}."
        )
    background = [window for window in observation.windows if not window.foreground][:12]
    if background:
        lines.append("Other visible top-level windows (use switch_window with the id when useful):")
        for window in background:
            lines.append(
                f"- id={window.window_id}; title={window.title!r}; process={window.process_name!r}"
            )
    hints: list[str] = []
    for control in observation.controls[:40]:
        if not control.enabled:
            continue
        try:
            point = control.rect.center_in(frame)
        except Exception:
            continue
        hints.append(
            f"- {control.control_type or 'control'} {control.name!r} near "
            f"({point.x:.3f},{point.y:.3f})"
        )
    if hints:
        lines.append("Advisory UIA hints:")
        lines.extend(hints)
    return "\n".join(lines)


class SingleLoopComputerRuntime(ComputerUseRuntime):
    """Computer Use driven by Loom's existing outer TurnRunner and current model.

    One model sampling step chooses one `computer_action`; Loom injects the OS
    input, re-observes the desktop, then attaches that fresh screenshot only to
    the next model request. No HostAgent/AppAgent, no nested grounder model, and
    no durable screenshot/base64 history are involved.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._computer_feedback_turns: dict[str, str] = {}
        self._computer_attempts: dict[tuple[str, str], deque[_Attempt]] = defaultdict(
            lambda: deque(maxlen=4)
        )

        # Replace the detailed diagnostics sink with one that strips UI text,
        # titles and error messages by key unless raw diagnostics were explicitly
        # requested. The old instance has emitted only its startup record so far.
        diagnostics = _SingleLoopComputerDiagnostics()
        self.computer_diagnostics = diagnostics
        if self.computer_sessions is not None:
            self.computer_sessions.diagnostics = diagnostics

        replacement = self._single_action_tool()
        rebuilt: list[AgentTool] = []
        replaced = False
        for tool in self.tools.all():
            if tool.name == "computer_action":
                rebuilt.append(replacement)
                replaced = True
            elif tool.name in {"computer_observe", "computer_step", "computer_run_task"}:
                rebuilt.append(replace(tool, exposure=ToolExposure.HIDDEN))
            else:
                rebuilt.append(tool)
        if not replaced and self.computer_sessions is not None:
            rebuilt.append(replacement)
        self.tools = ToolRegistry(tuple(rebuilt))

    def _single_action_tool(self) -> AgentTool:
        return AgentTool(
            name="computer_action",
            description=(
                "Loom's only model-facing desktop action tool. It uses the current conversation model and the normal "
                "TurnRunner: request screenshot first, then choose exactly one visual action from the returned image. "
                "Coordinates are normalized 0..1. Coordinate input is the primary execution path; UIA is only an "
                "advisory visual hint and no control id is accepted here. Every non-terminal action is followed by a "
                "fresh screenshot. If a coordinate pointer action injects successfully but causes no observable UI "
                "change, this tool returns ok=false so you must not assume the click worked."
            ),
            input_schema=_schema({"action": _single_action_schema()}, ("action",)),
            handler=self._handle_single_action,
            effect=ToolEffect.SENSITIVE,
        )

    def computer_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        status = dict(super().computer_status(owner_session_id))
        status.update(
            {
                "architecture": "single-model-single-loop",
                "model_control": "current conversation model via Loom TurnRunner",
                "model_facing_tools": ["computer_status", "computer_action"],
                "coordinate_execution": "primary",
                "uia_role": "advisory hints only for the single-loop path",
                "visual_feedback": "fresh screenshot attached ephemerally to the next model request",
                "legacy_grounder_active": False,
                "ufo_default_path": False,
            }
        )
        return status

    def _prepare_model_request(self, session, step, token):
        messages, extra = super()._prepare_model_request(session, step, token)
        store = self.computer_sessions
        if store is None:
            return messages, extra
        if self._computer_feedback_turns.get(session.session_id) != session.current_turn_id:
            return messages, extra
        try:
            snapshot = store.latest(session.session_id)
        except Exception:
            return messages, extra

        observation = snapshot.observation
        visual_message = AIMessage(
            role=MessageRole.USER,
            content=(
                TextPart(_model_observation_text(snapshot)),
                ImagePart(observation.image_data_url(), detail="auto"),
            ),
        )
        safe_extra = dict(extra) if isinstance(extra, dict) else {}
        safe_extra["computer_observation"] = {
            "state_revision": snapshot.state_revision,
            "image_bytes": len(observation.image_data),
            "image_media_type": observation.image_media_type,
            "frame_width": observation.frame.width,
            "frame_height": observation.frame.height,
            "windows_total": len(observation.windows),
            "controls_total": len(observation.controls),
        }
        return [*messages, visual_message], safe_extra

    def _mark_visual_feedback(self, context: ToolContext) -> None:
        self._computer_feedback_turns[context.session_id] = context.turn_id

    def _attempts_for(self, context: ToolContext) -> deque[_Attempt]:
        return self._computer_attempts[(context.session_id, context.turn_id)]

    @staticmethod
    def _attempt_from(
        action: ComputerAction,
        before: ComputerStateSnapshot,
        effect: str,
    ) -> _Attempt:
        frame = before.observation.frame
        return _Attempt(
            action=action,
            frame_origin_x=frame.origin_x,
            frame_origin_y=frame.origin_y,
            frame_width=frame.width,
            frame_height=frame.height,
            effect=str(effect or ""),
        )

    @staticmethod
    def _point_to_screen(attempt: _Attempt, *, end: bool = False) -> tuple[int, int] | None:
        point = attempt.action.end_point if end else attempt.action.point
        if point is None:
            return None
        return (
            attempt.frame_origin_x + round(point.x * max(0, attempt.frame_width - 1)),
            attempt.frame_origin_y + round(point.y * max(0, attempt.frame_height - 1)),
        )

    @classmethod
    def _same_attempt(cls, left: _Attempt, right: _Attempt) -> bool:
        if left.action.type is not right.action.type:
            return False
        if left.action.type is ComputerActionType.SWITCH_WINDOW:
            return bool(left.action.window_id) and left.action.window_id == right.action.window_id
        left_start = cls._point_to_screen(left)
        right_start = cls._point_to_screen(right)
        if left_start is None or right_start is None:
            return left.action.safe_dict() == right.action.safe_dict()
        if (
            abs(left_start[0] - right_start[0]) > _REPEAT_PIXEL_TOLERANCE
            or abs(left_start[1] - right_start[1]) > _REPEAT_PIXEL_TOLERANCE
        ):
            return False
        if left.action.type is not ComputerActionType.DRAG:
            return True
        left_end = cls._point_to_screen(left, end=True)
        right_end = cls._point_to_screen(right, end=True)
        return bool(
            left_end is not None
            and right_end is not None
            and abs(left_end[0] - right_end[0]) <= _REPEAT_PIXEL_TOLERANCE
            and abs(left_end[1] - right_end[1]) <= _REPEAT_PIXEL_TOLERANCE
        )

    def _should_block_repeat(
        self,
        context: ToolContext,
        action: ComputerAction,
        before: ComputerStateSnapshot,
    ) -> bool:
        if action.type not in _POINTER_ACTIONS:
            return False
        history = tuple(self._attempts_for(context))
        if len(history) < 2:
            return False
        current = self._attempt_from(action, before, "")
        return all(
            previous.effect == "unchanged" and self._same_attempt(previous, current)
            for previous in history[-2:]
        )

    def _record_attempt(
        self,
        context: ToolContext,
        action: ComputerAction,
        before: ComputerStateSnapshot,
        effect: str,
    ) -> None:
        self._attempts_for(context).append(self._attempt_from(action, before, effect))

    def _trace_effect(self, context: ToolContext, outcome: ComputerStepOutcome) -> None:
        try:
            self.computer_diagnostics.trace(
                context.session_id,
                context.turn_id,
                "single_loop.action_effect",
                action=outcome.prediction.action.safe_dict(),
                verification=dict(outcome.verification),
            )
        except Exception:
            pass

    def _handle_single_action(
        self,
        context: ToolContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        context.raise_if_cancelled()
        store = self.computer_sessions
        if store is None:
            return ToolResult(False, "Computer Use is unavailable on this host.")
        raw = arguments.get("action")
        if not isinstance(raw, dict):
            raise ValueError("computer_action action must be an object")
        action_payload = dict(raw)
        action_name = str(action_payload.get("type") or "").strip().casefold()

        if action_name == "screenshot":
            snapshot = store.observe(context.session_id)
            self._mark_visual_feedback(context)
            return ToolResult(
                True,
                "Desktop screenshot captured. Inspect the attached screenshot in the next model step and choose exactly one next action.",
                {"observation": _safe_snapshot_data(snapshot), "effect": "observed"},
            )

        if action_name == "done":
            return ToolResult(
                True,
                "No desktop input was injected. If the user's visible task is complete, answer the user now without another Computer Use action.",
                {"effect": "not_applicable", "terminal_hint": "done"},
            )
        if action_name == "call_user":
            return ToolResult(
                True,
                "No desktop input was injected. Ask the user for the missing information or decision in your next response.",
                {"effect": "not_applicable", "terminal_hint": "call_user"},
            )

        try:
            before = store.latest(context.session_id)
        except RuntimeError:
            snapshot = store.observe(context.session_id)
            self._mark_visual_feedback(context)
            return ToolResult(
                False,
                "No current desktop screenshot existed, so Loom captured one instead of executing a blind action. Inspect the attached screenshot and retry with one visible action.",
                {"observation": _safe_snapshot_data(snapshot), "effect": "not_executed"},
            )

        if action_name == "type" and "text" in action_payload:
            action_payload["text"] = self.consume_computer_transient(
                str(action_payload.get("text") or "")
            )
        action = ComputerAction.from_dict(action_payload)

        if self._should_block_repeat(context, action, before):
            self._mark_visual_feedback(context)
            self._record_attempt(context, action, before, "unchanged")
            return ToolResult(
                False,
                "Not executed: the same pointer target already produced no observable change twice. Inspect the current screenshot and choose a different target or strategy.",
                {
                    "action": action.safe_dict(),
                    "effect": "unchanged",
                    "effect_reason": "repeated_no_effect_blocked_before_input",
                    "stuck_detected": True,
                    "observation": _safe_snapshot_data(before),
                },
            )

        outcome = store.execute(context.session_id, before.state_revision, action)
        outcome.verification.update(_classify_effect(outcome))
        effect = str(outcome.verification.get("effect") or "uncertain")
        self._record_attempt(context, action, outcome.before, effect)
        self._mark_visual_feedback(context)
        self._trace_effect(context, outcome)

        execution_ok = bool(outcome.execution is not None and outcome.execution.ok)
        pointer_no_effect = action.type in _POINTER_ACTIONS and effect == "unchanged"
        target_failed = (
            action.type is ComputerActionType.SWITCH_WINDOW
            and outcome.verification.get("target_confirmed") is False
        )
        ok = execution_ok and not pointer_no_effect and not target_failed

        if not execution_ok:
            content = "The desktop input failed to execute. Inspect the attached current screenshot and choose another action."
        elif pointer_no_effect:
            content = (
                "Input was injected successfully, but no observable UI change followed this pointer action. "
                "Treat the action as not having worked; inspect the attached screenshot and do not blindly repeat the same target."
            )
        elif target_failed:
            content = "The requested window did not become the foreground window. Inspect the attached screenshot and choose another strategy."
        elif effect == "changed":
            content = "Computer action executed and produced an observable desktop change. Inspect the attached fresh screenshot before choosing the next action."
        else:
            content = "Computer action executed. Its visible effect is uncertain; inspect the attached fresh screenshot before choosing the next action."

        return ToolResult(ok, content, _safe_outcome_data(outcome))

    def clear_computer_single_loop_state(self, session_id: str) -> None:
        self._computer_feedback_turns.pop(str(session_id or ""), None)
        stale = [key for key in self._computer_attempts if key[0] == str(session_id or "")]
        for key in stale:
            self._computer_attempts.pop(key, None)

    def set_permission_mode(self, session_id, mode):
        self.clear_computer_single_loop_state(session_id)
        return super().set_permission_mode(session_id, mode)

    def recover_interrupted(self, session_id):
        self.clear_computer_single_loop_state(session_id)
        return super().recover_interrupted(session_id)

    def close(self) -> None:
        self._computer_feedback_turns.clear()
        self._computer_attempts.clear()
        super().close()


__all__ = ["SingleLoopComputerRuntime"]
