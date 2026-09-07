from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any, Sequence

from .computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerObservation,
    ComputerPoint,
    ComputerPrediction,
    ComputerTrajectoryEntry,
)


GUI_PLUS_DEFAULT_MODEL = "gui-plus-2026-02-26"
GUI_PLUS_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
GUI_PLUS_GROUNDER_ALIASES = frozenset(
    {
        "alibaba-gui-plus",
        "aliyun-gui-plus",
        "dashscope-gui-plus",
        "gui-plus",
    }
)


_GUI_PLUS_SYSTEM_PROMPT = """You are Loom's visual GUI grounding policy. Choose exactly one next desktop action from the current screenshot.

The screenshot is represented to you in a model coordinate space of 1000x1000. Coordinates are frame-local: (0,0) is the top-left and (1000,1000) is the bottom-right. Click visible targets near their centers.

Return exactly two things: one short `Action:` line and one `<tool_call>` block. The block must contain one JSON object with name `computer_use` and an `arguments` object.

Supported actions and arguments:
- left_click, double_click, right_click, mouse_move: `coordinate: [x, y]`
- left_click_drag: `coordinate: [start_x, start_y]`, `coordinate2: [end_x, end_y]`
- key: `keys: [key, ...]`
- type: `text: string`
- scroll: `pixels: number`, optionally `coordinate: [x, y]`; positive scrolls up and negative scrolls down
- wait: `time: seconds`
- terminate: `status: success|failure`
- interact or answer: `text: string`

Do not emit middle_click, triple_click, hscroll, shell commands, file operations, or multiple actions. Loom owns the outer agent loop, permissions, retries, and task completion. Use terminate only when the screenshot shows that the GUI task is complete or cannot proceed.
"""


class AlibabaGUIPlusGroundingBackend:
    """Alibaba GUI-Plus one-step grounding adapter for Loom Computer Use.

    Provider-specific prompt syntax, XML tool-call parsing and request options stay
    at this boundary. The rest of Loom only receives ``ComputerPrediction`` and
    therefore remains independent from GUI-Plus protocol details.

    Credentials are consumed while constructing the OpenAI-compatible client and
    are deliberately not retained on this object or exposed through status data.
    """

    name = "alibaba-gui-plus"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = GUI_PLUS_DEFAULT_BASE_URL,
        model: str = GUI_PLUS_DEFAULT_MODEL,
        client: Any | None = None,
        request_timeout_seconds: float = 120.0,
        max_controls: int = 100,
        max_trajectory: int = 8,
        system_prompt: str = _GUI_PLUS_SYSTEM_PROMPT,
        high_resolution_images: bool = True,
        enable_thinking: bool = False,
    ) -> None:
        api_key = str(api_key or "").strip()
        base_url = str(base_url or "").strip().rstrip("/")
        model = str(model or "").strip()
        if not api_key:
            raise ValueError("Alibaba GUI-Plus grounding backend requires an API key")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("Alibaba GUI-Plus base_url must be a complete http/https URL")
        if not model:
            raise ValueError("Alibaba GUI-Plus model must not be empty")
        timeout = float(request_timeout_seconds)
        if not 10.0 <= timeout <= 600.0:
            raise ValueError("request_timeout_seconds must be within 10..600")

        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - core dependency in normal installs
                raise RuntimeError("openai Python SDK is required for Alibaba GUI-Plus") from exc
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
            )

        self.client = client
        self.base_url = base_url
        self.model = model
        self.request_timeout_seconds = timeout
        self.max_controls = max(0, int(max_controls))
        self.max_trajectory = max(0, int(max_trajectory))
        self.system_prompt = str(system_prompt or "").strip()
        self.high_resolution_images = bool(high_resolution_images)
        self.enable_thinking = bool(enable_thinking)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        client: Any | None = None,
    ) -> "AlibabaGUIPlusGroundingBackend | None":
        env = os.environ if environ is None else environ
        api_key = _first_env(env, "LOOM_COMPUTER_API_KEY", "DASHSCOPE_API_KEY")
        if not api_key:
            return None
        base_url = _first_env(env, "LOOM_COMPUTER_BASE_URL") or GUI_PLUS_DEFAULT_BASE_URL
        model = _first_env(env, "LOOM_COMPUTER_MODEL") or GUI_PLUS_DEFAULT_MODEL
        timeout_text = _first_env(env, "LOOM_COMPUTER_TIMEOUT")
        timeout = float(timeout_text) if timeout_text else 120.0
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            client=client,
            request_timeout_seconds=timeout,
            high_resolution_images=_env_bool(env, "LOOM_COMPUTER_HIGH_RES", default=True),
            enable_thinking=_env_bool(env, "LOOM_COMPUTER_ENABLE_THINKING", default=False),
        )

    def predict(
        self,
        instruction: str,
        observation: ComputerObservation,
        trajectory: Sequence[ComputerTrajectoryEntry] = (),
    ) -> ComputerPrediction:
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("computer grounding instruction must not be empty")

        controls: list[str] = []
        for control in observation.controls[: self.max_controls]:
            try:
                point = control.rect.center_in(observation.frame)
            except ValueError:
                continue
            controls.append(
                f"{control.control_id}: type={control.control_type!r}, name={control.name!r}, "
                f"point=({round(point.x * 1000)},{round(point.y * 1000)}), enabled={control.enabled}"
            )
        control_text = "\n".join(controls) if controls else "(no usable UIA controls)"

        history = tuple(trajectory)[-self.max_trajectory :] if self.max_trajectory else ()
        history_text = "\n".join(item.prompt_line() for item in history) if history else "(none)"
        active_title = observation.active_window.title if observation.active_window is not None else ""
        prompt = (
            f"Instruction: {instruction}\n"
            f"Current screenshot source={observation.frame.source}, active_window={active_title!r}.\n\n"
            f"Recent trajectory:\n{history_text}\n\n"
            f"UI Automation hints (their points also use the 0..1000 model frame):\n{control_text}\n"
        )

        extra_body: dict[str, object] = {
            "vl_high_resolution_images": self.high_resolution_images,
            "enable_thinking": self.enable_thinking,
        }
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": observation.image_data_url()},
                            },
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
                temperature=0.01,
                max_tokens=1200,
                timeout=self.request_timeout_seconds,
                extra_body=extra_body,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Alibaba GUI-Plus request failed: {type(exc).__name__}: {exc}"
            ) from exc

        choices = getattr(response, "choices", None) or ()
        if not choices:
            raise RuntimeError("Alibaba GUI-Plus returned no choices")
        message = getattr(choices[0], "message", None)
        text = str(getattr(message, "content", "") or "").strip() if message is not None else ""
        if not text:
            raise RuntimeError("Alibaba GUI-Plus returned an empty prediction")
        return parse_gui_plus_prediction(text)

    def safe_config(self) -> dict[str, object]:
        """Return non-secret diagnostics suitable for Computer Use status."""

        return {
            "model": self.model,
            "base_url": self.base_url,
            "high_resolution_images": self.high_resolution_images,
            "enable_thinking": self.enable_thinking,
        }


def parse_gui_plus_prediction(text: str) -> ComputerPrediction:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("GUI-Plus prediction must not be empty")

    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", raw, flags=re.DOTALL | re.IGNORECASE)
    if match is None:
        raise ValueError("GUI-Plus prediction is missing <tool_call>")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("GUI-Plus tool call contains invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("GUI-Plus tool call must be a JSON object")
    if str(payload.get("name") or "").strip() != "computer_use":
        raise ValueError("GUI-Plus tool call must target computer_use")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("GUI-Plus computer_use arguments must be a JSON object")

    action_name = str(arguments.get("action") or "").strip().casefold()
    thought_match = re.search(r"(?:^|\n)\s*Action:\s*(.*?)(?=\n\s*<tool_call>|$)", raw, flags=re.DOTALL)
    thought = thought_match.group(1).strip() if thought_match else ""

    if action_name in {"left_click", "click"}:
        action = ComputerAction(type=ComputerActionType.CLICK, point=_gui_plus_point(arguments, "coordinate"))
    elif action_name in {"double_click", "triple_click"}:
        action = ComputerAction(
            type=ComputerActionType.DOUBLE_CLICK,
            point=_gui_plus_point(arguments, "coordinate"),
        )
    elif action_name == "right_click":
        action = ComputerAction(
            type=ComputerActionType.RIGHT_CLICK,
            point=_gui_plus_point(arguments, "coordinate"),
        )
    elif action_name == "mouse_move":
        action = ComputerAction(type=ComputerActionType.MOVE, point=_gui_plus_point(arguments, "coordinate"))
    elif action_name in {"left_click_drag", "drag"}:
        action = ComputerAction(
            type=ComputerActionType.DRAG,
            point=_gui_plus_point(arguments, "coordinate"),
            end_point=_gui_plus_point(arguments, "coordinate2"),
        )
    elif action_name == "key":
        keys = _gui_plus_keys(arguments.get("keys"))
        action = ComputerAction(
            type=ComputerActionType.HOTKEY if len(keys) > 1 else ComputerActionType.KEY,
            keys=keys,
        )
    elif action_name == "type":
        action = ComputerAction(type=ComputerActionType.TYPE, text=str(arguments.get("text") or ""))
    elif action_name == "scroll":
        pixels = float(arguments.get("pixels", 0.0))
        if pixels == 0:
            raise ValueError("GUI-Plus scroll requires non-zero pixels")
        point = _gui_plus_point(arguments, "coordinate", required=False)
        action = ComputerAction(
            type=ComputerActionType.SCROLL,
            point=point,
            direction="up" if pixels > 0 else "down",
            amount=max(120, min(3600, round(abs(pixels) * 120))),
        )
    elif action_name == "wait":
        seconds = float(arguments.get("time", 1.0))
        if seconds < 0:
            raise ValueError("GUI-Plus wait time must not be negative")
        action = ComputerAction(
            type=ComputerActionType.WAIT,
            duration_ms=max(50, min(30_000, round(seconds * 1000))),
        )
    elif action_name == "terminate":
        status = str(arguments.get("status") or "success").strip().casefold()
        if status not in {"success", "failure"}:
            raise ValueError("GUI-Plus terminate status must be success or failure")
        action = ComputerAction(
            type=ComputerActionType.FINISH if status == "success" else ComputerActionType.CALL_USER
        )
        if status == "failure" and not thought:
            thought = "GUI-Plus reported that the GUI task could not be completed."
    elif action_name in {"interact", "answer"}:
        action = ComputerAction(type=ComputerActionType.CALL_USER)
        text_value = str(arguments.get("text") or "").strip()
        if text_value and not thought:
            thought = text_value
    else:
        raise ValueError(f"unsupported GUI-Plus action: {action_name or '<empty>'}")

    return ComputerPrediction(action=action, thought=thought)


def _gui_plus_point(
    arguments: Mapping[str, object],
    key: str,
    *,
    required: bool = True,
) -> ComputerPoint | None:
    value = arguments.get(key)
    if value is None:
        if required:
            raise ValueError(f"GUI-Plus action requires {key}")
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"GUI-Plus {key} must be [x, y]")
    try:
        x = float(value[0])
        y = float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GUI-Plus {key} values must be numbers") from exc
    if not 0.0 <= x <= 1000.0 or not 0.0 <= y <= 1000.0:
        raise ValueError(f"GUI-Plus {key} values must be within 0..1000")
    return ComputerPoint(x=x / 1000.0, y=y / 1000.0)


def _gui_plus_keys(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = [part for part in re.split(r"[+\s]+", value) if part]
    elif isinstance(value, (list, tuple)):
        values = [str(part or "").strip() for part in value if str(part or "").strip()]
    else:
        values = []
    if not values:
        raise ValueError("GUI-Plus key action requires keys")
    return tuple(value.casefold() for value in values)


def _first_env(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _env_bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = str(env.get(name) or "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false")


__all__ = [
    "AlibabaGUIPlusGroundingBackend",
    "GUI_PLUS_DEFAULT_BASE_URL",
    "GUI_PLUS_DEFAULT_MODEL",
    "GUI_PLUS_GROUNDER_ALIASES",
    "parse_gui_plus_prediction",
]
