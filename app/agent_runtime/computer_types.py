from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class ComputerActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MOVE = "move"
    DRAG = "drag"
    SCROLL = "scroll"
    TYPE = "type"
    HOTKEY = "hotkey"
    KEY = "key"
    SWITCH_WINDOW = "switch_window"
    WAIT = "wait"
    FINISH = "finish"
    CALL_USER = "call_user"


@dataclass(frozen=True, slots=True)
class ComputerPoint:
    """Frame-local normalized point in the inclusive 0..1 range."""

    x: float
    y: float

    def __post_init__(self) -> None:
        x = float(self.x)
        y = float(self.y)
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError("computer point coordinates must be normalized to 0..1")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True, slots=True)
class ComputerRect:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        left = int(self.left)
        top = int(self.top)
        right = int(self.right)
        bottom = int(self.bottom)
        if right <= left or bottom <= top:
            raise ValueError("computer rect must have positive width and height")
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "top", top)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "bottom", bottom)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def center_in(self, frame: "ComputerFrame") -> ComputerPoint:
        return ComputerPoint(
            x=((self.left + self.right) / 2 - frame.origin_x) / frame.width,
            y=((self.top + self.bottom) / 2 - frame.origin_y) / frame.height,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }


@dataclass(frozen=True, slots=True)
class ComputerFrame:
    """Physical-pixel coordinate frame used for one screenshot.

    origin_x/origin_y are virtual-desktop physical pixel coordinates and may be
    negative on multi-monitor Windows layouts. Model-facing coordinates are always
    normalized relative to this frame rather than carrying a process-global DPI
    scale factor.
    """

    frame_id: str
    origin_x: int
    origin_y: int
    width: int
    height: int
    source: str = "active_window"
    window_id: str = ""
    monitor_id: str = ""
    dpi_x: int = 96
    dpi_y: int = 96

    def __post_init__(self) -> None:
        frame_id = str(self.frame_id or "").strip()
        source = str(self.source or "active_window").strip()
        if not frame_id:
            raise ValueError("computer frame requires frame_id")
        width = int(self.width)
        height = int(self.height)
        if width < 1 or height < 1:
            raise ValueError("computer frame dimensions must be positive")
        object.__setattr__(self, "frame_id", frame_id)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "origin_x", int(self.origin_x))
        object.__setattr__(self, "origin_y", int(self.origin_y))
        object.__setattr__(self, "dpi_x", max(1, int(self.dpi_x)))
        object.__setattr__(self, "dpi_y", max(1, int(self.dpi_y)))
        object.__setattr__(self, "window_id", str(self.window_id or ""))
        object.__setattr__(self, "monitor_id", str(self.monitor_id or ""))

    def to_screen(self, point: ComputerPoint) -> tuple[int, int]:
        x = self.origin_x + round(point.x * max(0, self.width - 1))
        y = self.origin_y + round(point.y * max(0, self.height - 1))
        return x, y

    def to_dict(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "source": self.source,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "width": self.width,
            "height": self.height,
            "window_id": self.window_id,
            "monitor_id": self.monitor_id,
            "dpi_x": self.dpi_x,
            "dpi_y": self.dpi_y,
        }


@dataclass(frozen=True, slots=True)
class ComputerWindow:
    window_id: str
    title: str
    process_name: str = ""
    rect: ComputerRect | None = None
    foreground: bool = False

    def __post_init__(self) -> None:
        window_id = str(self.window_id or "").strip()
        if not window_id:
            raise ValueError("computer window requires window_id")
        object.__setattr__(self, "window_id", window_id)
        object.__setattr__(self, "title", str(self.title or ""))
        object.__setattr__(self, "process_name", str(self.process_name or ""))
        object.__setattr__(self, "foreground", bool(self.foreground))

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "window_id": self.window_id,
            "title": self.title,
            "process_name": self.process_name,
            "foreground": self.foreground,
        }
        if self.rect is not None:
            payload["rect"] = self.rect.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class ComputerControl:
    control_id: str
    name: str
    control_type: str
    rect: ComputerRect
    automation_id: str = ""
    enabled: bool = True
    source: str = "uia"

    def __post_init__(self) -> None:
        control_id = str(self.control_id or "").strip()
        if not control_id:
            raise ValueError("computer control requires control_id")
        object.__setattr__(self, "control_id", control_id)
        object.__setattr__(self, "name", str(self.name or ""))
        object.__setattr__(self, "control_type", str(self.control_type or ""))
        object.__setattr__(self, "automation_id", str(self.automation_id or ""))
        object.__setattr__(self, "source", str(self.source or "uia"))
        object.__setattr__(self, "enabled", bool(self.enabled))

    def to_dict(self, *, frame: ComputerFrame | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "control_id": self.control_id,
            "name": self.name,
            "type": self.control_type,
            "automation_id": self.automation_id,
            "enabled": self.enabled,
            "source": self.source,
            "rect": self.rect.to_dict(),
        }
        if frame is not None:
            try:
                payload["point"] = self.rect.center_in(frame).to_dict()
            except ValueError:
                pass
        return payload


@dataclass(frozen=True, slots=True)
class ComputerObservation:
    observation_id: str
    frame: ComputerFrame
    image_png: bytes
    active_window: ComputerWindow | None = None
    windows: tuple[ComputerWindow, ...] = ()
    controls: tuple[ComputerControl, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        observation_id = str(self.observation_id or "").strip()
        if not observation_id:
            raise ValueError("computer observation requires observation_id")
        image_png = bytes(self.image_png)
        if not image_png:
            raise ValueError("computer observation requires screenshot bytes")
        windows = tuple(self.windows)
        controls = tuple(self.controls)
        if any(not isinstance(item, ComputerWindow) for item in windows):
            raise TypeError("windows must contain ComputerWindow values")
        if any(not isinstance(item, ComputerControl) for item in controls):
            raise TypeError("controls must contain ComputerControl values")
        object.__setattr__(self, "observation_id", observation_id)
        object.__setattr__(self, "image_png", image_png)
        object.__setattr__(self, "windows", windows)
        object.__setattr__(self, "controls", controls)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def image_sha256(self) -> str:
        return hashlib.sha256(self.image_png).hexdigest()

    def image_data_url(self) -> str:
        encoded = base64.b64encode(self.image_png).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def to_safe_dict(
        self,
        *,
        control_limit: int = 80,
        redactor=None,
    ) -> dict[str, object]:
        def clean(value: str) -> str:
            text = str(value or "")
            return str(redactor(text)) if callable(redactor) else text

        controls: list[dict[str, object]] = []
        for control in self.controls[: max(0, int(control_limit))]:
            item = control.to_dict(frame=self.frame)
            item["name"] = clean(str(item.get("name", "")))[:500]
            item["automation_id"] = clean(str(item.get("automation_id", "")))[:300]
            controls.append(item)

        windows: list[dict[str, object]] = []
        for window in self.windows[:24]:
            item = window.to_dict()
            item["title"] = clean(str(item.get("title", "")))[:500]
            item["process_name"] = clean(str(item.get("process_name", "")))[:200]
            windows.append(item)

        active = self.active_window.to_dict() if self.active_window is not None else None
        if active is not None:
            active["title"] = clean(str(active.get("title", "")))[:500]
            active["process_name"] = clean(str(active.get("process_name", "")))[:200]

        def clean_json(value: Any) -> Any:
            if isinstance(value, str):
                return clean(value)[:2000]
            if isinstance(value, Mapping):
                return {str(k)[:200]: clean_json(v) for k, v in list(value.items())[:64]}
            if isinstance(value, (list, tuple)):
                return [clean_json(item) for item in value[:64]]
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return clean(str(value))[:2000]

        return {
            "observation_id": self.observation_id,
            "image_sha256": self.image_sha256,
            "image_bytes": len(self.image_png),
            "frame": self.frame.to_dict(),
            "active_window": active,
            "windows": windows,
            "controls": controls,
            "controls_total": len(self.controls),
            "controls_truncated": len(self.controls) > len(controls),
            "metadata": clean_json(dict(self.metadata)),
        }


@dataclass(frozen=True, slots=True)
class ComputerAction:
    type: ComputerActionType
    point: ComputerPoint | None = None
    end_point: ComputerPoint | None = None
    control_id: str = ""
    window_id: str = ""
    text: str = ""
    keys: tuple[str, ...] = ()
    direction: str = "down"
    amount: int = 700
    duration_ms: int = 400

    def __post_init__(self) -> None:
        action_type = ComputerActionType(self.type)
        keys = tuple(str(item or "").strip() for item in self.keys if str(item or "").strip())
        direction = str(self.direction or "down").strip().casefold()
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("computer scroll direction must be up/down/left/right")
        object.__setattr__(self, "type", action_type)
        object.__setattr__(self, "control_id", str(self.control_id or "").strip())
        object.__setattr__(self, "window_id", str(self.window_id or "").strip())
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "amount", max(1, min(10_000, int(self.amount))))
        object.__setattr__(self, "duration_ms", max(0, min(30_000, int(self.duration_ms))))
        self._validate_required_fields()

    def _validate_required_fields(self) -> None:
        target_actions = {
            ComputerActionType.CLICK,
            ComputerActionType.DOUBLE_CLICK,
            ComputerActionType.RIGHT_CLICK,
            ComputerActionType.MOVE,
        }
        if self.type in target_actions and self.point is None and not self.control_id:
            raise ValueError(f"{self.type.value} requires point or control_id")
        if self.type is ComputerActionType.DRAG:
            if self.point is None or self.end_point is None:
                raise ValueError("drag requires point and end_point")
        if self.type is ComputerActionType.TYPE and not self.text:
            raise ValueError("type requires non-empty text")
        if self.type in {ComputerActionType.HOTKEY, ComputerActionType.KEY} and not self.keys:
            raise ValueError(f"{self.type.value} requires keys")
        if self.type is ComputerActionType.SWITCH_WINDOW and not self.window_id:
            raise ValueError("switch_window requires window_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ComputerAction":
        if not isinstance(value, Mapping):
            raise TypeError("computer action must be an object")
        point = _point_from_mapping(value, "point")
        if point is None and ("x" in value or "y" in value):
            point = ComputerPoint(float(value.get("x", 0.0)), float(value.get("y", 0.0)))
        end_point = _point_from_mapping(value, "end_point")
        if end_point is None and ("x2" in value or "y2" in value):
            end_point = ComputerPoint(float(value.get("x2", 0.0)), float(value.get("y2", 0.0)))
        raw_keys = value.get("keys") or []
        if isinstance(raw_keys, str):
            raw_keys = [raw_keys]
        if not isinstance(raw_keys, Sequence):
            raise ValueError("computer action keys must be an array")
        return cls(
            type=ComputerActionType(str(value.get("type") or "")),
            point=point,
            end_point=end_point,
            control_id=str(value.get("control_id") or ""),
            window_id=str(value.get("window_id") or ""),
            text=str(value.get("text") or ""),
            keys=tuple(str(item) for item in raw_keys),
            direction=str(value.get("direction") or "down"),
            amount=int(value.get("amount", 700)),
            duration_ms=int(value.get("duration_ms", 400)),
        )

    def safe_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"type": self.type.value}
        if self.point is not None:
            payload["point"] = self.point.to_dict()
        if self.end_point is not None:
            payload["end_point"] = self.end_point.to_dict()
        if self.control_id:
            payload["control_id"] = self.control_id
        if self.window_id:
            payload["window_id"] = self.window_id
        if self.text:
            payload["text"] = "[TRANSIENT_TEXT]"
            payload["text_length"] = len(self.text)
        if self.keys:
            payload["keys"] = list(self.keys)
        if self.type is ComputerActionType.SCROLL:
            payload["direction"] = self.direction
            payload["amount"] = self.amount
        if self.type is ComputerActionType.DRAG:
            payload["duration_ms"] = self.duration_ms
        return payload


@dataclass(frozen=True, slots=True)
class ComputerExecution:
    ok: bool
    message: str
    action: ComputerAction
    native: bool = False
    fallback_used: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "ok": bool(self.ok),
            "message": str(self.message or ""),
            "action": self.action.safe_dict(),
            "native": bool(self.native),
            "fallback_used": bool(self.fallback_used),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ComputerPrediction:
    action: ComputerAction
    thought: str = ""


@dataclass(frozen=True, slots=True)
class ComputerTrajectoryEntry:
    instruction: str
    observation_id: str
    image_sha256: str
    action: ComputerAction
    execution_ok: bool

    def prompt_line(self) -> str:
        action = self.action.safe_dict()
        return (
            f"observation={self.observation_id}; image={self.image_sha256[:12]}; action={action}; "
            f"execution={'ok' if self.execution_ok else 'failed'}"
        )


def _point_from_mapping(value: Mapping[str, Any], key: str) -> ComputerPoint | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"{key} must be an object")
    return ComputerPoint(float(raw.get("x", 0.0)), float(raw.get("y", 0.0)))


__all__ = [
    "ComputerAction",
    "ComputerActionType",
    "ComputerControl",
    "ComputerExecution",
    "ComputerFrame",
    "ComputerObservation",
    "ComputerPoint",
    "ComputerPrediction",
    "ComputerRect",
    "ComputerTrajectoryEntry",
    "ComputerWindow",
]
