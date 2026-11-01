from __future__ import annotations

import base64
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any, Mapping

from app.ai import AIMessage, ImagePart, MessageRole

from .computer_diagnostics import ComputerDiagnostics
from .computer_runtime import (
    ComputerSessionStore,
    ComputerStateSnapshot,
    ComputerStepOutcome,
    ComputerUseRuntime,
)
from .computer_types import ComputerAction, ComputerActionType, ComputerFrame, ComputerPoint
from .contracts import AgentEventKind, ToolEffect
from .tools import AgentTool, ToolContext, ToolExposure, ToolRegistry, ToolResult


# Pointer actions are the ones where a successful input injection can still be a
# unverified result: Windows accepted the mouse event, but the observer cannot
# prove the application result. ok=false is conservative, not a claim that no
# DOM event occurred.
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

# Windows refuses foreground changes driven by synthetic input, so these
# combinations reliably inject and just as reliably do nothing: the switcher
# needs a real, physically held modifier. They are refused rather than executed
# because "the keys went in" would otherwise be reported as a successful action,
# and the model would burn a step plus a full re-observation discovering that
# the foreground window never moved. switch_window is the supported intent and
# has a real success check, so the refusal points there.
_WINDOW_SWITCH_HOTKEYS = (
    frozenset({"alt", "tab"}),
    frozenset({"alt", "shift", "tab"}),
    frozenset({"alt", "esc"}),
    frozenset({"alt", "escape"}),
    frozenset({"win", "tab"}),
    frozenset({"winleft", "tab"}),
    frozenset({"winright", "tab"}),
    frozenset({"super", "tab"}),
    frozenset({"command", "tab"}),
)


# Chords that act on the whole session rather than on the task's application.
# Each one either destroys the state the task depends on or hands the machine
# back to the user in a state they did not ask for, and none of them is ever the
# only way to do something. Refusing costs one step; recovering from "every
# window is now minimised" costs many, as a real trace showed when Win+D removed
# the target application from the window list mid-task.
_DESTRUCTIVE_HOTKEYS: dict[frozenset[str], str] = {
    frozenset({"win", "d"}): "shows the desktop, minimising every window including the task's",
    frozenset({"winleft", "d"}): "shows the desktop, minimising every window including the task's",
    frozenset({"win", "m"}): "minimises every window including the task's",
    frozenset({"winleft", "m"}): "minimises every window including the task's",
    frozenset({"win", "l"}): "locks the workstation and ends the session",
    frozenset({"winleft", "l"}): "locks the workstation and ends the session",
}


def _window_switch_hotkey(action: ComputerAction) -> bool:
    if action.type not in {ComputerActionType.HOTKEY, ComputerActionType.KEY}:
        return False
    keys = frozenset(str(key or "").strip().casefold() for key in action.keys)
    return keys in _WINDOW_SWITCH_HOTKEYS


def _destructive_hotkey(action: ComputerAction) -> str:
    """Why this chord must not be injected, or "" if it is fine."""

    if action.type not in {ComputerActionType.HOTKEY, ComputerActionType.KEY}:
        return ""
    keys = frozenset(str(key or "").strip().casefold() for key in action.keys)
    return _DESTRUCTIVE_HOTKEYS.get(keys, "")


_VISUAL_SAMPLE_SIZE = (128, 72)
_VISUAL_PIXEL_DELTA = 12
_VISUAL_CHANGE_RATIO = 0.0015

# A batch is the actions one screenshot is enough for: click a field and type,
# fill a form, confirm with Enter. Model round trips are ~90% of a Computer Use
# turn's time, so each action that rides along saves one. Six covers a form
# without letting a stale plan run far past what the screenshot showed.
MAX_BATCH_ACTIONS = 6
_BATCH_EXCLUDED = frozenset({"screenshot", "done", "call_user"})
_KEYBOARD_ACTIONS = frozenset(
    {ComputerActionType.TYPE, ComputerActionType.CLEAR_TEXT, ComputerActionType.HOTKEY, ComputerActionType.KEY}
)

# Before a later batch action acts on a point, the patch around that point must
# still look as it did in the screenshot the model planned from. A wide change
# there means the layout moved or something now covers the target. The patch is
# a few percent of the frame; JPEG noise stays far below the change ratio.
_TARGET_PATCH_FRACTION = 0.025
_TARGET_PATCH_MIN_PX = 12
_TARGET_PIXEL_DELTA = 16
_TARGET_CHANGE_RATIO = 0.25

# Evidence that a pointer action's own target repainted (a caret, a focus ring,
# a checkbox tick) when the coarse whole-frame sample saw nothing. A caret is
# about 1x16 px; JPEG re-encoding of unchanged pixels is deterministic, so a
# handful of strongly changed pixels at the target is a real repaint. Carets
# blink, so a click that shows nothing is looked at once more after a pause.
_LOCAL_PIXEL_DELTA = 24
_LOCAL_MIN_CHANGED = 8
_RECHECK_DELAY_S = 0.35
_ROW_BAND_FRACTION = 0.3

_BATCH_STOP_MESSAGES = {
    "foreground_changed": "the foreground window changed, so later actions could reach a different application",
    "window_moved": "the window moved or resized, so the planned coordinates no longer point where they did",
    "target_region_changed": "the area around the next target changed since the screenshot, so it may have moved or be covered",
    "target_unverifiable": "the next target could not be compared with the screenshot",
    "action_not_confirmed": "the previous action failed or its effect could not be confirmed",
}

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


class _NoGrounder:
    """Constructor sentinel that prevents legacy visual-model auto-discovery."""

    name = "disabled-single-loop"


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
                    "clear_text",
                    "hotkey",
                    "key",
                    "switch_window",
                    "wait",
                    "done",
                    "call_user",
                ],
            },
            "point": point,
            "frame_id": {"type": "string", "maxLength": 128},
            "control_id": {"type": "string", "maxLength": 128},
            "end_point": point,
            "window_id": {"type": "string", "maxLength": 128},
            "text": {"type": "string", "maxLength": 20000,
                     "description": "Literal text to type. Never copy loom-transient-computer references from previous tool history: those are private one-shot handles, not input text."},
            "keys": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "maxItems": 8,
            },
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 10000},
            "duration_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
            "button": {"type": "string", "enum": ["left", "right"]},
        },
        ("type",),
    )


def _batch_action_schema() -> dict[str, Any]:
    # Items are described by reference rather than repeating every field of
    # `action`: the full copy doubled this tool's schema on every request, and
    # the handler validates each item as strictly as a single action anyway.
    allowed = [
        name for name in _single_action_schema()["properties"]["type"]["enum"] if name not in _BATCH_EXCLUDED
    ]
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": allowed}},
            "required": ["type"],
            "description": "One action with the same fields as `action`.",
        },
        "minItems": 1,
        "maxItems": MAX_BATCH_ACTIONS,
        "description": (
            "Several actions planned from the same screenshot, run in order. Loom stops at the first action that "
            "fails or cannot be confirmed, when the foreground window changes or moves, or when the area around a "
            "later target no longer matches the screenshot; the rest are not sent."
        ),
    }


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


def _patch_change(
    earlier, later, point: ComputerPoint, *, delta: int, half_width_fraction: float = 0.0
) -> tuple[int, int] | None:
    """(changed, total) pixels at full resolution in the patch around ``point``, or None.

    The patch is square unless ``half_width_fraction`` widens it into a band of
    the same rows.
    """

    try:
        from PIL import Image

        with Image.open(BytesIO(earlier.image_data)) as left_image:
            left = left_image.convert("L")
        with Image.open(BytesIO(later.image_data)) as right_image:
            right = right_image.convert("L")
        if left.size != right.size:
            right = right.resize(left.size)
        width, height = left.size
        radius = max(_TARGET_PATCH_MIN_PX, round(_TARGET_PATCH_FRACTION * width))
        half_width = max(radius, round(half_width_fraction * width))
        cx, cy = round(point.x * (width - 1)), round(point.y * (height - 1))
        box = (max(0, cx - half_width), max(0, cy - radius), min(width, cx + half_width + 1), min(height, cy + radius + 1))
        before_bytes = left.crop(box).tobytes()
        after_bytes = right.crop(box).tobytes()
        if not before_bytes or len(before_bytes) != len(after_bytes):
            return None
        changed = sum(1 for a, b in zip(before_bytes, after_bytes) if abs(a - b) >= delta)
        return changed, len(before_bytes)
    except Exception:
        return None


def _target_region_changed(planned, current, point: ComputerPoint) -> bool | None:
    """Whether the patch around ``point`` changed between two same-frame observations.

    None means the comparison could not be made, which a caller must not read
    as "unchanged".
    """

    counts = _patch_change(planned, current, point, delta=_TARGET_PIXEL_DELTA)
    if counts is None:
        return None
    return counts[0] / counts[1] > _TARGET_CHANGE_RATIO


def _local_change_at(before, after, action: ComputerAction) -> bool:
    """Whether the action's own target repainted, seen at full resolution.

    The whole-frame check samples 128x72, where a text caret or a checkbox tick
    vanishes: a live run (2026-11-01) reported both a focus click into an empty
    field and a click that really ticked a checkbox as "no observable change",
    which ended a batch after its first step and sent the model back to one
    action per round trip.
    """

    for point in (action.point, action.end_point):
        if point is None:
            continue
        counts = _patch_change(before, after, point, delta=_LOCAL_PIXEL_DELTA)
        if counts is not None and counts[0] >= _LOCAL_MIN_CHANGED:
            return True
        # The caret of an empty field appears at its start, not under the
        # click in its middle (the second live run, 2026-11-01): accept a
        # repaint in the same rows across the width a text field can span.
        counts = _patch_change(before, after, point, delta=_LOCAL_PIXEL_DELTA,
                               half_width_fraction=_ROW_BAND_FRACTION)
        if counts is not None and counts[0] >= _LOCAL_MIN_CHANGED:
            return True
    return False


def _same_frame_geometry(left: ComputerFrame, right: ComputerFrame) -> bool:
    return (left.origin_x, left.origin_y, left.width, left.height) == (
        right.origin_x, right.origin_y, right.width, right.height
    )


def _batch_stop_reason(planned: ComputerStateSnapshot, current: ComputerStateSnapshot, action: ComputerAction) -> str:
    """Why the next batch action must not run on a plan made from ``planned``, or ""."""

    if action.type is ComputerActionType.SWITCH_WINDOW or action.type is ComputerActionType.WAIT:
        return ""
    if ComputerSessionStore._window_id_key(planned.observation.frame.window_id) != ComputerSessionStore._window_id_key(
        current.observation.frame.window_id
    ):
        return "foreground_changed"
    points = [point for point in (action.point, action.end_point) if point is not None]
    if not points:
        return ""
    if not _same_frame_geometry(planned.observation.frame, current.observation.frame):
        return "window_moved"
    for point in points:
        changed = _target_region_changed(planned.observation, current.observation, point)
        if changed is None:
            return "target_unverifiable"
        if changed:
            return "target_region_changed"
    return ""


_HINT_PRIORITY = frozenset(
    {"Button", "Edit", "Hyperlink", "CheckBox", "ComboBox", "MenuItem", "RadioButton", "TabItem", "Slider"}
)
_HINT_LIMIT = 40


def _hint_controls(observation) -> list:
    """The UIA controls the model is shown this step, actionable kinds first."""

    frame = observation.frame
    hints = []
    for control in sorted(observation.controls, key=lambda c: c.control_type not in _HINT_PRIORITY):
        if len(hints) >= _HINT_LIMIT:
            break
        if not control.enabled:
            continue
        try:
            control.rect.center_in(frame)
        except Exception:
            continue
        hints.append(control)
    return hints


def _mark_label(control) -> str:
    return str(control.control_id).rsplit(":", 1)[-1]


def _set_of_mark_enabled() -> bool:
    return str(os.environ.get("LOOM_COMPUTER_SET_OF_MARK", "1")).strip().casefold() not in {"0", "false", "off", "no"}


def _set_of_mark_image(observation, controls) -> tuple[bytes, str] | None:
    """The screenshot with each hinted control boxed and numbered (Set-of-Mark).

    Coordinates the model estimates by eye are the weak point of a screenshot
    loop; a numbered box lets it name a control instead, and Loom clicks the
    control's real center. Only the model-facing copy is marked: verification
    keeps comparing the untouched captures.
    """

    if not controls:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        frame = observation.frame
        with Image.open(BytesIO(observation.image_data)) as source:
            image = source.convert("RGB")
        scale_x = image.width / max(1, frame.width)
        scale_y = image.height / max(1, frame.height)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=max(11, round(13 * min(1.0, scale_x * 1.4))))
        colour = (230, 0, 160)
        for control in controls:
            rect = control.rect
            left = (rect.left - frame.origin_x) * scale_x
            top = (rect.top - frame.origin_y) * scale_y
            right = (rect.right - frame.origin_x) * scale_x
            bottom = (rect.bottom - frame.origin_y) * scale_y
            if right <= 0 or bottom <= 0 or left >= image.width or top >= image.height:
                continue
            draw.rectangle((left, top, right, bottom), outline=colour, width=2)
            label = _mark_label(control)
            text_box = draw.textbbox((0, 0), label, font=font)
            tag_w, tag_h = text_box[2] - text_box[0] + 4, text_box[3] - text_box[1] + 3
            # The tag sits above the box so it does not cover the control's own label.
            tag_x = max(0, left)
            tag_y = top - tag_h if top >= tag_h else max(0, top)
            draw.rectangle((tag_x, tag_y, tag_x + tag_w, tag_y + tag_h), fill=colour)
            draw.text((tag_x + 2, tag_y + 1 - text_box[1]), label, fill=(255, 255, 255), font=font)
        output = BytesIO()
        media_type = str(observation.image_media_type or "image/png")
        if media_type == "image/jpeg":
            # Same settings as the capture encoder: no chroma subsampling keeps small text legible.
            image.save(output, format="JPEG", quality=80, optimize=True, subsampling=0)
        else:
            image.save(output, format="PNG")
            media_type = "image/png"
        return output.getvalue(), media_type
    except Exception:
        return None


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
    active_window_changed = (
        ComputerSessionStore._window_id_key(before_window)
        != ComputerSessionStore._window_id_key(after_window)
    )
    ratio = _visual_delta_ratio(before.observation, after.observation)
    visual_changed = ratio >= _VISUAL_CHANGE_RATIO if ratio >= 0 else bool(base.get("visual_changed"))
    target_confirmed = base.get("target_confirmed")

    semantic_verified = bool(execution.native)
    if action.type is ComputerActionType.TYPE:
        semantic_verified = bool(execution.details.get("content_verified"))
    elif action.type is ComputerActionType.CLEAR_TEXT:
        semantic_verified = bool(execution.native and execution.details.get("after_ime_composition") is False)
    if action.type is ComputerActionType.SWITCH_WINDOW and target_confirmed is True:
        effect = "changed" if active_window_changed else "noop"
        reason = "foreground_window_confirmed" if active_window_changed else "already_foreground"
        semantic_verified = True
    elif active_window_changed:
        # A foreground change is success only for an explicit window switch.
        # For typing, keys and pointer input it means the post-action observer
        # can no longer prove which window consumed the input.  Calling that
        # ``changed`` caused the model to turn focus races into false passes.
        effect = "uncertain"
        reason = "foreground_target_lost_after_input"
        semantic_verified = False
    elif semantic_verified and action.type is ComputerActionType.TYPE:
        effect = "changed"
        reason = "focused_value_readback_matched"
    elif visual_changed:
        effect = "changed"
        reason = "observable_visual_change"
    elif action.type in _POINTER_ACTIONS and _local_change_at(before.observation, after.observation, action):
        visual_changed = True
        effect = "changed"
        reason = "local_change_at_target"
    elif action.type in _POINTER_ACTIONS and not execution.native:
        effect = "uncertain"
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
        "semantic_verified": semantic_verified,
        "noop": effect == "noop",
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
        "semantics": dict(observation.semantics),
    }


def _screen_point(outcome: ComputerStepOutcome) -> dict[str, float] | None:
    """Where on the whole desktop the action landed, normalized 0..1.

    The action's own point is normalized against the captured frame, which is
    one application window somewhere on the desktop. Anything drawing a marker
    over the real screen needs the same physical place expressed against the
    virtual screen instead, and only this layer holds the frame to convert with.
    """

    action = outcome.prediction.action
    point = action.point or action.end_point
    if point is None:
        return None
    return desktop_point(outcome.before.observation.frame, point)


def desktop_point(frame: ComputerFrame, point: ComputerPoint) -> dict[str, float] | None:
    """Express one frame-local point against the whole virtual screen.

    Anything drawing over the real desktop - the HUD overlay above all - needs
    this conversion rather than the action's own coordinates. A point is
    normalized against the captured application window, so handing that fraction
    to a full-screen overlay marks the right fraction of the wrong rectangle.
    """

    screen_x, screen_y = frame.to_screen(point)
    bounds = _virtual_screen_bounds()
    if bounds is None:
        return None
    left, top, width, height = bounds
    if width < 1 or height < 1:
        return None
    return {
        "screen_x": float(screen_x),
        "screen_y": float(screen_y),
        "x_norm": min(1.0, max(0.0, (screen_x - left) / width)),
        "y_norm": min(1.0, max(0.0, (screen_y - top) / height)),
    }


def _virtual_screen_bounds() -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
            int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
            int(user32.GetSystemMetrics(78)),  # SM_CXVIRTUALSCREEN
            int(user32.GetSystemMetrics(79)),  # SM_CYVIRTUALSCREEN
        )
    except Exception:
        return None


def _safe_outcome_data(outcome: ComputerStepOutcome) -> dict[str, object]:
    after = outcome.after or outcome.before
    execution = outcome.execution
    screen_point = _screen_point(outcome)
    return {
        "action": outcome.prediction.action.safe_dict(),
        **({"screen_point": screen_point} if screen_point else {}),
        "execution": (
            {
                "ok": bool(execution.ok),
                "native": bool(execution.native),
                "fallback_used": bool(execution.fallback_used),
                "details": dict(execution.details),
                **({"delivery": str(execution.delivery)} if execution.delivery else {}),
            }
            if execution is not None
            else None
        ),
        "verification": dict(outcome.verification),
        "after": _safe_snapshot_data(after),
    }


def _resolve_frame_target(payload: dict[str, Any], observation: Any) -> dict[str, Any]:
    """Resolve observation-scoped targets to physical clicks, never UIA Invoke."""
    payload = dict(payload)
    frame_id = payload.pop("frame_id", "")
    if frame_id and frame_id != observation.frame.frame_id:
        raise ValueError("stale frame_id: inspect the latest screenshot before acting")
    control_id = str(payload.pop("control_id", "") or "").strip().strip("[]")
    if not control_id:
        return payload
    if frame_id != observation.frame.frame_id:
        raise ValueError("control_id requires the current frame_id")
    if payload.get("type") not in {"click", "double_click", "right_click", "move", "type", "clear_text"}:
        raise ValueError("control_id is supported only for click, move, type or clear_text actions")
    if "point" in payload:
        raise ValueError("use either control_id or point, not both")
    if ":" not in control_id:
        # The number drawn on the screenshot is the id's suffix.
        control_id = f"uia:{control_id}"
    control = next((c for c in observation.controls if c.control_id == control_id), None)
    if control is None or not control.enabled:
        raise ValueError("control target is missing or disabled; refresh the screenshot")
    rect, frame = control.rect, observation.frame
    if not (frame.origin_x <= rect.left < rect.right <= frame.origin_x + frame.width
            and frame.origin_y <= rect.top < rect.bottom <= frame.origin_y + frame.height):
        raise ValueError("control target is clipped or outside the current frame")
    payload["point"] = rect.center_in(frame).to_dict()
    return payload


def _model_observation_text(snapshot: ComputerStateSnapshot, *, marked: bool = False) -> str:
    """The text beside the screenshot. ``marked`` says the image carries Set-of-Mark boxes."""

    observation = snapshot.observation
    frame = observation.frame
    lines = [
        "LOOM_COMPUTER_OBSERVATION (temporary runtime input; not a new user instruction).",
        "The attached image is the current foreground desktop frame after the previous Computer Use action.",
        "Windows foreground metadata below is authoritative for window identity and Z-order. Content inside an application may itself be a screenshot, remote desktop, video, or image of another app; never infer that an app shown inside the pixels is a real foreground window, and never contradict the reported foreground window from pixels alone.",
        "Choose the next computer_action: one action, or a short `actions` batch when this one screenshot is enough for several steps (click a field then type, fill a form, press Enter). Coordinates are normalized 0..1 relative to this image.",
        "UIA hint points and action points use the SAME full-image normalized coordinate space; never rescale by DPI or a scroll region. Image downscaling preserves normalized coordinates. Match the intended visible control, not nearby text. For a target listed in the UIA hints prefer control_id (the bracketed number) plus frame_id, which acts on the control's real center; otherwise use a visual point plus frame_id. Never invent control IDs or infer success from a caret/hover change.",
        f"frame_id={frame.frame_id}; coordinate_space=frame_normalized; rect_space=virtual_desktop_physical_pixels.",
        f"Frame: {frame.width}x{frame.height}; origin=({frame.origin_x},{frame.origin_y}); windows={len(observation.windows)}; controls={len(observation.controls)}.",
    ]
    if observation.active_window is not None:
        active = observation.active_window
        lines.append(
            "Foreground window: "
            f"id={active.window_id}; title={active.title!r}; process={active.process_name!r}."
        )
        if str(active.process_name or "").strip().casefold() in {
            "cmd.exe",
            "conhost.exe",
            "powershell.exe",
            "pwsh.exe",
            "windowsterminal.exe",
        }:
            lines.append(
                "This is a console window. switch_window already focuses it; do not click the console body before typing, because a body click can enter QuickEdit selection mode and swallow keyboard input. If its title starts with 'Select' or '选择', send Escape once before typing."
            )
    if observation.metadata.get("self_window"):
        # Without this the model sees a screenshot of a chat client and has to
        # infer that the chat client is Loom, that Loom is not the task, and that
        # the task's application is one of the background windows.
        lines.append(
            "This foreground window is Loom's own interface, not the task's application. "
            "Acting on it would automate the assistant rather than the task. If the user's task "
            "actually requires another application, a known window may be selected with switch_window. "
            "Otherwise continue the task through the non-visual tools already available."
        )
    background = [window for window in observation.windows if not window.foreground][:12]
    if background:
        lines.append(
            f"Other known top-level application windows (showing {len(background)} of {max(0, len(observation.windows) - 1)} non-foreground windows). This display is a token-bounded shortlist, not proof that omitted applications are closed. It may include minimized or tray-hidden recoverable windows; use switch_window with the id instead of searching the desktop/taskbar when the target app is already listed:"
        )
        for window in background:
            lines.append(
                f"- id={window.window_id}; title={window.title!r}; process={window.process_name!r}"
            )
    hints: list[str] = []
    for control in _hint_controls(observation):
        point = control.rect.center_in(frame)
        hints.append(
            f"- [{_mark_label(control)}] id={control.control_id}; {control.control_type or 'control'} {control.name!r} near "
            f"({point.x:.3f},{point.y:.3f})"
        )
    if hints:
        lines.append(
            "UIA hints (each is boxed in magenta on the image with its number):" if marked else "UIA hints:"
        )
        lines.extend(hints)
        if len(observation.controls) > len(hints) or observation.semantics.get("state") == "partial":
            lines.append("UIA hints are incomplete. An omitted target is not absent; do not invent its type or coordinates. Inspect the full attached image, not a cropped preview, and verify the intended application result after clicking.")
    else:
        # Say why the hints are missing rather than letting the model infer that
        # the window is empty. Hints are deadline-bounded, so "not collected" is
        # a routine outcome on a busy desktop and must not look like evidence.
        state = str(observation.semantics.get("state") or "")
        if state in {"unavailable", "partial", "skipped"}:
            lines.append(
                "No UIA hints this step (semantic layer was not collected within its latency budget). "
                "This says nothing about the window's contents: read the screenshot and act on visual coordinates."
            )
    return "\n".join(lines)


class SingleLoopComputerRuntime(ComputerUseRuntime):
    """Computer Use driven by Loom's existing outer TurnRunner and current model.

    One model sampling step chooses one `computer_action`; Loom injects the OS
    input, re-observes the desktop, then attaches that fresh screenshot only to
    the next model request. No HostAgent/AppAgent, no nested grounder model, and
    no durable screenshot/base64 history are involved.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Seal the architecture here rather than relying on the product assembly
        # layer. Stale GUI-Plus/UI-TARS environment variables must never create a
        # second visual model when this runtime class is used directly.
        kwargs["computer_grounder"] = _NoGrounder()
        kwargs["computer_model_profile"] = None
        kwargs["computer_grounder_kind"] = ""
        super().__init__(*args, **kwargs)
        if self.computer_sessions is not None:
            self.computer_sessions.grounder = None
        self.computer_model_profile = ""
        self.computer_grounder_name = "disabled"
        self.computer_grounder_kind = "disabled"

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
                "Loom's only model-facing desktop action tool. Reach for it when a task genuinely needs the GUI, "
                "not as the default way to work: it is an ordinary tool in the normal loop, so prefer a cheaper route "
                "when one exists and freely interleave the two within a single task. Launching or closing an "
                "application, reading or writing a file, querying state, and anything scriptable are all faster and "
                "more reliable through exec than through clicking, and exec has no application allowlist. Use this "
                "tool for what only the GUI can do. "
                "It uses the current conversation model and the normal "
                "TurnRunner: request screenshot first, then choose the next step from the returned image. "
                "Send one `action`, or an `actions` batch when the same screenshot is enough for several steps - "
                "click a field and type, fill a form, confirm with Enter - since every separate call costs a full "
                "model round trip. Loom stops a batch at the first action that fails or cannot be confirmed, or when "
                "the window or the area around the next target changed, and the remaining actions are not sent. "
                "Coordinates are normalized 0..1. A UIA hint can be targeted by control_id (its number) plus frame_id "
                "for click, move, type and clear_text; Loom acts on the control's real center. If a target application already appears in "
                "the known-window list, use switch_window rather than searching the desktop or taskbar; hidden/tray "
                "windows may be recoverable this way. Every non-terminal action is followed by a fresh screenshot. "
                "Treat the reported Windows foreground id/process as authoritative for window identity: an image, "
                "video, remote desktop, or prior screenshot displayed inside an app is content, not evidence that "
                "the pictured app is above it in the real desktop Z-order. "
                "Use clear_text as one atomic action to clear the focused editor instead of issuing separate Ctrl+A "
                "and Delete calls whose focus can change between steps. A pixel delta only proves repainting; unless "
                "semantic_verified is true, do not claim that text, selection, or drag intent succeeded. "
                "If a coordinate pointer action injects successfully but causes no observable UI change, this tool "
                "returns ok=false so you must not assume the click worked."
            ),
            input_schema=_schema({"action": _single_action_schema(), "actions": _batch_action_schema()}),
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
                "uia_role": "advisory numbered hints (Set-of-Mark on the screenshot); control_id resolves to the control center",
                "batch_actions": f"up to {MAX_BATCH_ACTIONS}, guarded between actions",
                "visual_feedback": "fresh screenshot attached ephemerally to the next model request",
                "window_reuse": "prefer switch_window for known visible/minimized/tray-hidden app windows",
                "legacy_grounder_active": False,
                "ufo_default_path": False,
            }
        )
        return status

    def _collect_model_observations(self, session, step):
        messages, extra = super()._collect_model_observations(session, step)
        store = self.computer_sessions
        if store is None:
            return messages, extra
        marked_turn = self._computer_feedback_turns.get(session.session_id)
        if marked_turn != session.current_turn_id:
            # A terminal/abandoned turn may never reach its promised follow-up
            # sample. Do not retain that stale marker indefinitely or allow it
            # to affect a later turn.
            if marked_turn is not None:
                self._computer_feedback_turns.pop(session.session_id, None)
            return messages, extra
        try:
            snapshot = store.latest(session.session_id)
        except Exception:
            return messages, extra

        # Visual feedback is a one-shot continuation input. Leaving this marker
        # set for the whole turn reattached the same desktop observation after
        # every later exec/read/edit tool, repeatedly steering the model back to
        # Computer Use and preventing otherwise completed coding work from
        # reaching a final answer. A new computer_action explicitly marks the
        # next observation again.
        self._computer_feedback_turns.pop(session.session_id, None)

        observation = snapshot.observation
        source_call_id = next((message.tool_call_id for message in reversed(session.messages)
                               if message.role is MessageRole.TOOL
                               and (message.name or "").startswith("computer_")), "")
        from .tool_observation import attach_observation
        marked = _set_of_mark_image(observation, _hint_controls(observation)) if _set_of_mark_enabled() else None
        if marked is not None:
            image_url = f"data:{marked[1]};base64,{base64.b64encode(marked[0]).decode('ascii')}"
        else:
            image_url = observation.image_data_url()
        messages = attach_observation(messages, _model_observation_text(snapshot, marked=marked is not None),
            tool_prefix="computer_", tool_call_id=source_call_id,
            image=ImagePart(image_url, detail="auto"))
        safe_extra = dict(extra) if isinstance(extra, dict) else {}
        safe_extra["computer_observation"] = {
            "source_call_id": source_call_id,
            "state_revision": snapshot.state_revision,
            "image_bytes": len(observation.image_data),
            "image_media_type": observation.image_media_type,
            "frame_width": observation.frame.width,
            "frame_height": observation.frame.height,
            "windows_total": len(observation.windows),
            "controls_total": len(observation.controls),
            "set_of_mark": marked is not None,
        }
        return messages, safe_extra

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
            previous.effect in {"unchanged", "unobserved_pointer"} and self._same_attempt(previous, current)
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
        batch = arguments.get("actions")
        raw = arguments.get("action")
        if batch is not None:
            if raw is not None:
                raise ValueError("computer_action takes either action or actions, not both")
            return self._handle_batch(context, store, batch)
        if not isinstance(raw, dict):
            raise ValueError("computer_action needs action (one object) or actions (a list)")
        action_payload = dict(raw)
        action_name = str(action_payload.get("type") or "").strip().casefold()

        if action_name == "screenshot":
            snapshot = store.observe(context.session_id)
            self._mark_visual_feedback(context)
            return ToolResult(
                True,
                "Desktop screenshot captured. Inspect the attached screenshot in the next model step and choose the next action.",
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

        before = self._current_or_observe(context, store)
        if isinstance(before, ToolResult):
            return before
        action = self._prepare_action(context, action_payload, before)
        refusal = self._refusal(context, action, before)
        if refusal is not None:
            self._mark_visual_feedback(context)
            return refusal
        outcome, ok, content = self._run_action(context, store, action, before)
        self._mark_visual_feedback(context)
        return ToolResult(ok, content, _safe_outcome_data(outcome))

    def _current_or_observe(self, context: ToolContext, store) -> ComputerStateSnapshot | ToolResult:
        try:
            return store.latest(context.session_id)
        except RuntimeError:
            snapshot = store.observe(context.session_id)
            self._mark_visual_feedback(context)
            return ToolResult(
                False,
                "No current desktop screenshot existed, so Loom captured one instead of executing a blind action. Inspect the attached screenshot and retry with one visible action.",
                {"observation": _safe_snapshot_data(snapshot), "effect": "not_executed"},
            )

    def _prepare_action(
        self,
        context: ToolContext,
        payload: dict[str, Any],
        before: ComputerStateSnapshot,
    ) -> ComputerAction:
        payload = dict(payload)
        name = str(payload.get("type") or "").strip().casefold()
        button = str(payload.pop("button", "") or "").strip().casefold()
        if name == "click" and button == "right":
            payload["type"] = "right_click"
        if str(payload.get("type") or "") == "type" and "text" in payload:
            # The session id is not optional here. Typed text is stashed by the
            # platform that produced the response, and a session with its own
            # model has its own platform wrapper; consuming without the session
            # id looks the handle up in the default platform instead, never
            # finds it, and fails every type action with "no longer available".
            payload["text"] = self.consume_computer_transient(
                str(payload.get("text") or ""),
                context.session_id,
            )
        return ComputerAction.from_dict(_resolve_frame_target(payload, before.observation))

    def _refusal(
        self,
        context: ToolContext,
        action: ComputerAction,
        before: ComputerStateSnapshot,
    ) -> ToolResult | None:
        """A ToolResult when this action must not be injected at all, else None."""

        destructive = _destructive_hotkey(action)
        if destructive:
            return ToolResult(
                False,
                f"Not executed: that shortcut {destructive}. It acts on the whole session rather than on "
                "the application this task is about. Work with the target window directly - switch_window "
                "brings a known window forward without disturbing anything else.",
                {
                    "action": action.safe_dict(),
                    "effect": "not_applicable",
                    "effect_reason": "destructive_global_hotkey_refused",
                    "observation": _safe_snapshot_data(before),
                },
            )

        if _window_switch_hotkey(action):
            known = [
                f"{window.window_id} ({window.process_name})"
                for window in before.observation.windows[:12]
            ]
            return ToolResult(
                False,
                "Not executed: Windows ignores synthetic Alt+Tab style switching, so this would inject "
                "keys and leave the foreground window unchanged. Use switch_window with the id of the "
                "target window instead"
                + (f"; known windows: {', '.join(known)}." if known else "."),
                {
                    "action": action.safe_dict(),
                    "effect": "not_applicable",
                    "effect_reason": "synthetic_window_switch_hotkey_refused",
                    "observation": _safe_snapshot_data(before),
                },
            )

        if self._should_block_repeat(context, action, before):
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
        return None

    def _run_action(
        self,
        context: ToolContext,
        store,
        action: ComputerAction,
        before: ComputerStateSnapshot,
    ) -> tuple[ComputerStepOutcome, bool, str]:
        outcome = store.execute(context.session_id, before.state_revision, action)
        outcome.verification.update(_classify_effect(outcome))
        if outcome.verification.get("effect_reason") == "no_observable_change_after_pointer_input":
            outcome = self._look_again(context, store, action, outcome)
        effect = str(outcome.verification.get("effect") or "uncertain")
        self._record_attempt(context, action, outcome.before,
                             "unobserved_pointer" if outcome.verification.get("effect_reason") == "no_observable_change_after_pointer_input" else effect)
        self._trace_effect(context, outcome)

        execution_ok = bool(outcome.execution is not None and outcome.execution.ok)
        semantic_verified = bool(outcome.verification.get("semantic_verified"))
        pointer_no_effect = action.type in _POINTER_ACTIONS and outcome.verification.get("effect_reason") == "no_observable_change_after_pointer_input"
        target_failed = (
            action.type is ComputerActionType.SWITCH_WINDOW
            and outcome.verification.get("target_confirmed") is False
        )
        ok = execution_ok and not pointer_no_effect and not target_failed

        if not execution_ok:
            content = "The desktop input failed to execute. Inspect the attached current screenshot and choose another action."
            if outcome.execution is not None and outcome.execution.message:
                content += f" ({outcome.execution.message})"
        elif pointer_no_effect:
            content = (
                "Input was injected successfully, but no observable UI change was detected after this pointer action. "
                "Its semantic result is uncertain, not proof that the application received no event. Check application state and do not blindly repeat the same target."
            )
        elif target_failed:
            content = "The requested window did not become the foreground window. Inspect the attached screenshot and choose another strategy."
        elif effect == "noop":
            content = "Target was already foreground; no window switch was needed. Target identity is verified, not a state change."
        elif effect == "changed" and semantic_verified:
            content = "Computer action executed and its intended semantic result was verified. Inspect the attached fresh screenshot before choosing the next action."
        elif effect == "changed":
            content = (
                "Computer input was injected and pixels changed, but the intended semantic result was not verified. "
                "Do not report the action as successful from visual delta alone; inspect the fresh screenshot or use UIA state."
            )
        else:
            content = "Computer action executed. Its visible effect is uncertain; inspect the attached fresh screenshot before choosing the next action."
        return outcome, ok, content

    def _look_again(self, context: ToolContext, store, action: ComputerAction, outcome: ComputerStepOutcome):
        """One more look at a pointer action that showed nothing: carets blink and repaints lag.

        Costs one short pause and one observation, against a model round trip
        (and, in a batch, every step after it) when a real effect was missed.
        """

        time.sleep(_RECHECK_DELAY_S)
        try:
            later = store.observe(context.session_id)
        except Exception:
            return outcome
        before = outcome.before.observation
        ratio = _visual_delta_ratio(before, later.observation)
        moved = ComputerSessionStore._window_id_key(before.frame.window_id) != ComputerSessionStore._window_id_key(
            later.observation.frame.window_id
        )
        if moved or not (ratio >= _VISUAL_CHANGE_RATIO or _local_change_at(before, later.observation, action)):
            return replace(outcome, after=later)
        outcome.verification.update(
            {"effect": "changed", "effect_reason": "delayed_change_at_target", "visual_changed": True,
             "visual_delta_ratio": round(float(max(ratio, 0.0)), 6)}
        )
        return replace(outcome, after=later)

    def _handle_batch(self, context: ToolContext, store, batch: Any) -> ToolResult:
        """Run several actions planned from one screenshot, stopping at the first doubt.

        Every action goes through the same refusals, repeat block, execution and
        effect classification as a single action. Between actions the batch
        checks that the plan still applies (UFO2's speculative execution, with
        the screenshot standing in for UIA where controls are not exposed): the
        foreground window and its geometry are unchanged, the previous action
        executed and did not lose the foreground, and the area around the next
        target still looks as it did when the model planned. Anything else ends
        the batch with the remaining actions unsent and a fresh screenshot.
        """

        if not isinstance(batch, list) or not batch:
            raise ValueError("actions must be a non-empty list")
        if len(batch) > MAX_BATCH_ACTIONS:
            raise ValueError(f"actions takes at most {MAX_BATCH_ACTIONS} steps; send the rest after the next screenshot")
        payloads: list[dict[str, Any]] = []
        for item in batch:
            if not isinstance(item, dict):
                raise ValueError("each entry of actions must be an action object")
            name = str(item.get("type") or "").strip().casefold()
            if name in _BATCH_EXCLUDED:
                raise ValueError(f"{name} cannot be part of an actions batch; send it as a single action")
            payloads.append(dict(item))

        planned = self._current_or_observe(context, store)
        if isinstance(planned, ToolResult):
            return planned
        # Everything is validated against the screenshot the model planned from
        # before any input, so a malformed fourth step never leaves three done.
        actions = [self._prepare_action(context, payload, planned) for payload in payloads]

        steps: list[dict[str, Any]] = []
        last_outcome: ComputerStepOutcome | None = None
        last_ok, last_content = True, ""
        current = planned
        stopped_reason = ""
        refusal: ToolResult | None = None
        for index, action in enumerate(actions):
            context.raise_if_cancelled()
            if index:
                stopped_reason = _batch_stop_reason(planned, current, action)
                if stopped_reason:
                    break
            refusal = self._refusal(context, action, current)
            if refusal is not None:
                stopped_reason = f"refused:{refusal.data.get('effect_reason', 'refused')}"
                break
            outcome, last_ok, last_content = self._run_action(context, store, action, current)
            last_outcome = outcome
            current = outcome.after or current
            steps.append(
                {
                    "action": action.safe_dict(),
                    "ok": last_ok,
                    "effect": outcome.verification.get("effect"),
                    "effect_reason": outcome.verification.get("effect_reason"),
                    **(
                        {"delivery": outcome.execution.delivery}
                        if outcome.execution is not None and outcome.execution.delivery
                        else {}
                    ),
                }
            )
            # A lost foreground window is caught by the guard before the next
            # action, which also covers a planned switch_window followed by
            # steps that were aimed at the window it left.
            if index + 1 < len(actions) and not last_ok:
                stopped_reason = "action_not_confirmed"
                break
        self._mark_visual_feedback(context)

        executed = len(steps)
        stopped_at = executed + 1 if stopped_reason else None
        data: dict[str, Any] = (
            _safe_outcome_data(last_outcome) if last_outcome is not None else {"observation": _safe_snapshot_data(current)}
        )
        data["batch"] = {
            "requested": len(actions),
            "executed": executed,
            "stopped_reason": stopped_reason,
            "stopped_at": stopped_at,
            "steps": steps,
        }
        summary = f"Executed {executed} of {len(actions)} batched actions."
        if stopped_reason.startswith("refused:") and refusal is not None:
            summary += f" Action {stopped_at} was refused and nothing after it was sent: {refusal.content}"
        elif stopped_reason:
            why = _BATCH_STOP_MESSAGES.get(stopped_reason, stopped_reason)
            summary += (
                f" Stopped before action {stopped_at} because {why}; actions {stopped_at}-{len(actions)} were not sent. "
                "Inspect the attached fresh screenshot and plan the rest from it."
            )
        if last_content:
            summary += f" Last executed action: {last_content}"
        ok = not stopped_reason and last_ok
        return ToolResult(ok, summary, data)

    def clear_computer_single_loop_state(self, session_id: str) -> None:
        self._computer_feedback_turns.pop(str(session_id or ""), None)
        stale = [key for key in self._computer_attempts if key[0] == str(session_id or "")]
        for key in stale:
            self._computer_attempts.pop(key, None)

    def _on_turn_finished(self, session, event):
        super()._on_turn_finished(session, event)
        self.clear_computer_single_loop_state(session.session_id)

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
