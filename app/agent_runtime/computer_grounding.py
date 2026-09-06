from __future__ import annotations

import ast
import re
from typing import Protocol, Sequence

from app.ai import AIMessage, ChatRequest, ImagePart, MessageRole, TextPart, ToolChoice

from .computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerObservation,
    ComputerPoint,
    ComputerPrediction,
    ComputerTrajectoryEntry,
)


class ComputerGroundingBackend(Protocol):
    name: str

    def predict(
        self,
        instruction: str,
        observation: ComputerObservation,
        trajectory: Sequence[ComputerTrajectoryEntry] = (),
    ) -> ComputerPrediction:
        ...


_UI_TARS_SYSTEM_PROMPT = """You are the visual GUI policy for Loom Computer Use.
You receive one current screenshot, a compact Windows UI Automation summary, the user's current GUI instruction,
and a short action trajectory. Choose exactly one next GUI action.

Coordinates MUST be normalized to a 0..1000 frame-local coordinate system where (0,0) is the top-left of the
provided screenshot and (1000,1000) is the bottom-right. Never use virtual-desktop absolute pixels.

Output exactly:
Thought: one concise sentence
Action: one action call

Action space:
click(start_box='(x,y)')
left_double(start_box='(x,y)')
right_single(start_box='(x,y)')
drag(start_box='(x1,y1)', end_box='(x2,y2)')
hotkey(key='ctrl s')
type(content='text')
scroll(start_box='(x,y)', direction='down')
wait()
finished()
call_user()

Prefer a visible UIA control when its label clearly matches the requested target, but return coordinates rather than
control IDs. Do not claim success merely because an action was attempted; use finished() only when the current screen
shows the requested GUI task is complete.
"""


class UITarsGroundingBackend:
    """One-step UI-TARS-style visual policy adapter over Loom's provider-neutral AI platform.

    This is intentionally *not* the UI-TARS GUIAgent loop. Loom owns the outer
    Agent Runtime, permissions, cancellation, retries and durable history. This
    adapter performs one screenshot -> prediction operation only.
    """

    name = "ui-tars"

    def __init__(
        self,
        platform,
        profile_id: str,
        *,
        system_prompt: str = _UI_TARS_SYSTEM_PROMPT,
        max_controls: int = 100,
        max_trajectory: int = 8,
    ) -> None:
        profile_id = str(profile_id or "").strip().casefold()
        if not profile_id:
            raise ValueError("UI-TARS grounding backend requires profile_id")
        self.platform = platform
        self.profile_id = profile_id
        self.system_prompt = str(system_prompt or "").strip()
        self.max_controls = max(0, int(max_controls))
        self.max_trajectory = max(0, int(max_trajectory))

    def predict(
        self,
        instruction: str,
        observation: ComputerObservation,
        trajectory: Sequence[ComputerTrajectoryEntry] = (),
    ) -> ComputerPrediction:
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("computer grounding instruction must not be empty")

        controls = []
        for control in observation.controls[: self.max_controls]:
            try:
                point = control.rect.center_in(observation.frame)
            except ValueError:
                continue
            controls.append(
                f"{control.control_id}: type={control.control_type!r}, name={control.name!r}, "
                f"point=({point.x:.4f},{point.y:.4f}), enabled={control.enabled}"
            )
        control_text = "\n".join(controls) if controls else "(no usable UIA controls)"

        history = tuple(trajectory)[-self.max_trajectory :] if self.max_trajectory else ()
        history_text = "\n".join(item.prompt_line() for item in history) if history else "(none)"
        active_title = observation.active_window.title if observation.active_window is not None else ""

        prompt = (
            f"Instruction: {instruction}\n"
            f"Frame: {observation.frame.width}x{observation.frame.height}, source={observation.frame.source}, "
            f"active_window={active_title!r}\n\n"
            f"Recent trajectory:\n{history_text}\n\n"
            f"UI Automation candidates:\n{control_text}\n"
        )
        request = ChatRequest(
            messages=(
                AIMessage(role=MessageRole.SYSTEM, content=self.system_prompt),
                AIMessage(
                    role=MessageRole.USER,
                    content=(
                        TextPart(prompt),
                        ImagePart(observation.image_data_url(), detail="high"),
                    ),
                ),
            ),
            tool_choice=ToolChoice.NONE,
            temperature=0.0,
            max_output_tokens=800,
        )
        response = self.platform.execute_chat(self.profile_id, request)
        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            raise RuntimeError("UI-TARS grounding backend returned an empty prediction")
        return parse_ui_tars_prediction(text)


def parse_ui_tars_prediction(text: str, *, coordinate_scale: float = 1000.0) -> ComputerPrediction:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("UI-TARS prediction must not be empty")
    action_match = re.search(r"(?:^|\n)\s*Action:\s*(.+?)\s*$", raw, flags=re.DOTALL)
    if action_match is None:
        raise ValueError("UI-TARS prediction is missing Action:")
    action_source = action_match.group(1).strip()
    # A model may append a second paragraph after the call. Keep only the first
    # syntactically complete call rather than evaluating arbitrary text.
    action_source = _first_call_expression(action_source)
    function, kwargs = _parse_call(action_source)
    thought_match = re.search(r"(?:^|\n)\s*Thought:\s*(.*?)(?=\n\s*Action:|$)", raw, flags=re.DOTALL)
    thought = thought_match.group(1).strip() if thought_match else ""

    aliases = {
        "click": ComputerActionType.CLICK,
        "left_click": ComputerActionType.CLICK,
        "left_single": ComputerActionType.CLICK,
        "left_double": ComputerActionType.DOUBLE_CLICK,
        "double_click": ComputerActionType.DOUBLE_CLICK,
        "right_single": ComputerActionType.RIGHT_CLICK,
        "right_click": ComputerActionType.RIGHT_CLICK,
        "drag": ComputerActionType.DRAG,
        "scroll": ComputerActionType.SCROLL,
        "type": ComputerActionType.TYPE,
        "hotkey": ComputerActionType.HOTKEY,
        "press": ComputerActionType.KEY,
        "wait": ComputerActionType.WAIT,
        "finished": ComputerActionType.FINISH,
        "finish": ComputerActionType.FINISH,
        "call_user": ComputerActionType.CALL_USER,
    }
    try:
        action_type = aliases[function]
    except KeyError as exc:
        raise ValueError(f"unsupported UI-TARS action: {function}") from exc

    point = _normalized_point(kwargs.get("start_box") or kwargs.get("point"), coordinate_scale)
    end_point = _normalized_point(kwargs.get("end_box"), coordinate_scale)
    if action_type is ComputerActionType.TYPE:
        action = ComputerAction(type=action_type, text=str(kwargs.get("content") or kwargs.get("text") or ""))
    elif action_type in {ComputerActionType.HOTKEY, ComputerActionType.KEY}:
        key_text = str(kwargs.get("key") or kwargs.get("hotkey") or "").strip()
        keys = tuple(item for item in re.split(r"[+\s]+", key_text) if item)
        action = ComputerAction(type=action_type, keys=keys)
    elif action_type is ComputerActionType.SCROLL:
        action = ComputerAction(
            type=action_type,
            point=point,
            direction=str(kwargs.get("direction") or "down"),
            amount=int(kwargs.get("amount") or 700),
        )
    elif action_type is ComputerActionType.DRAG:
        action = ComputerAction(type=action_type, point=point, end_point=end_point)
    elif action_type in {
        ComputerActionType.CLICK,
        ComputerActionType.DOUBLE_CLICK,
        ComputerActionType.RIGHT_CLICK,
    }:
        action = ComputerAction(type=action_type, point=point)
    else:
        action = ComputerAction(type=action_type)
    return ComputerPrediction(action=action, thought=thought)


def _first_call_expression(value: str) -> str:
    text = value.strip()
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[: index + 1]
    return text.splitlines()[0]


def _parse_call(source: str) -> tuple[str, dict[str, object]]:
    try:
        expression = ast.parse(source, mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"invalid UI-TARS action syntax: {source}") from exc
    if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
        raise ValueError("UI-TARS Action must be a simple function call")
    if expression.args:
        raise ValueError("UI-TARS Action positional arguments are not supported")
    kwargs: dict[str, object] = {}
    for keyword in expression.keywords:
        if keyword.arg is None:
            raise ValueError("UI-TARS Action **kwargs are not supported")
        try:
            kwargs[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"UI-TARS Action argument {keyword.arg!r} must be a literal") from exc
    return expression.func.id, kwargs


def _normalized_point(value: object, scale: float) -> ComputerPoint | None:
    if value is None or value == "":
        return None
    if isinstance(value, (tuple, list)):
        numbers = [float(item) for item in value]
    else:
        numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", str(value))]
    if len(numbers) not in {2, 4}:
        raise ValueError(f"invalid UI-TARS coordinate: {value!r}")
    if len(numbers) == 4:
        x = (numbers[0] + numbers[2]) / 2
        y = (numbers[1] + numbers[3]) / 2
    else:
        x, y = numbers
    scale = float(scale)
    if scale <= 0:
        raise ValueError("coordinate_scale must be positive")
    return ComputerPoint(x=x / scale, y=y / scale)


__all__ = [
    "ComputerGroundingBackend",
    "UITarsGroundingBackend",
    "parse_ui_tars_prediction",
]
