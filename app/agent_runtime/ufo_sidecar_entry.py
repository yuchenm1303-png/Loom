from __future__ import annotations

"""Loom-owned runtime patches for the pinned Microsoft UFO sidecar.

The upstream checkout remains byte-for-byte pinned. This entrypoint imports Loom's
normal sidecar, installs narrow compatibility/performance patches at runtime, and
then delegates to the original protocol implementation.
"""

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

try:
    from . import ufo_sidecar as core
except ImportError:  # Executed as a standalone script by the supervisor.
    import ufo_sidecar as core


CAPTURE_PROFILES: dict[str, dict[str, object]] = {
    "fast": {"media_type": "image/jpeg", "quality": 60, "max_pixels": 1_440_000},
    "balanced": {"media_type": "image/jpeg", "quality": 72, "max_pixels": 2_500_000},
    "high": {"media_type": "image/jpeg", "quality": 88, "max_pixels": 5_000_000},
    "lossless": {"media_type": "image/png", "quality": 0, "max_pixels": 0},
}
DEFAULT_CAPTURE_PROFILE = "balanced"
_MAX_WINDOW_CONTEXT = 20
_PATCHED = False
_CAPTURE_PROFILE = DEFAULT_CAPTURE_PROFILE
_ORIGINAL_BOOTSTRAP = core._bootstrap_ufo
_ORIGINAL_RUN_TASK = core._run_task


def _normalize_text(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _window_match_score(task: str, title: str, process_name: str = "") -> int:
    """Conservative score for an already-open window explicitly named by the task."""

    task_norm = _normalize_text(task)
    title_norm = _normalize_text(title)
    process_norm = _normalize_text(Path(str(process_name or "")).stem)
    score = 0
    if len(title_norm) >= 2 and title_norm in task_norm:
        score = max(score, 100 + min(40, len(title_norm)))
    if len(process_norm) >= 3 and process_norm in task_norm:
        score = max(score, 90 + min(30, len(process_norm)))
    return score


def _settings_candidates(ufo_root: Path | None) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if ufo_root is not None:
        root = Path(ufo_root).expanduser().resolve()
        # Normal layout: <LOOM_HOME>/drivers/ufo/<version>/src.
        if len(root.parents) >= 4:
            candidates.append(root.parents[3] / "settings.json")
    candidates.append(Path.home() / ".loom" / "settings.json")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _resolve_capture_profile(ufo_root: Path | None = None) -> str:
    explicit = str(os.environ.get("LOOM_UFO_SCREENSHOT_QUALITY") or "").strip().casefold()
    if explicit in CAPTURE_PROFILES:
        return explicit
    for settings_path in _settings_candidates(ufo_root):
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            computer = payload.get("computer") if isinstance(payload, dict) else None
            value = str((computer or {}).get("screenshotQuality") or "").strip().casefold()
            if value in CAPTURE_PROFILES:
                return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return DEFAULT_CAPTURE_PROFILE


def _encode_image_for_model(image: Any, profile_name: str | None = None) -> str:
    """Encode a PIL image for the model without changing UFO's screen geometry."""

    from PIL import Image

    if image is None:
        return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    name = str(profile_name or _CAPTURE_PROFILE).strip().casefold()
    profile = CAPTURE_PROFILES.get(name, CAPTURE_PROFILES[DEFAULT_CAPTURE_PROFILE])
    max_pixels = int(profile["max_pixels"] or 0)
    working = image
    if max_pixels and int(image.width) * int(image.height) > max_pixels:
        scale = (max_pixels / (int(image.width) * int(image.height))) ** 0.5
        resampling = getattr(Image, "Resampling", Image)
        working = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            resampling.LANCZOS,
        )
    media_type = str(profile["media_type"])
    output = io.BytesIO()
    if media_type == "image/jpeg":
        working.convert("RGB").save(
            output,
            format="JPEG",
            quality=int(profile["quality"]),
            optimize=True,
            subsampling=0,
        )
    else:
        working.save(output, format="PNG", optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _encode_path_for_model(image_path: str, _mime_type: str | None = None) -> str:
    from PIL import Image

    try:
        with Image.open(image_path) as image:
            image.load()
            return _encode_image_for_model(image)
    except Exception:
        # Preserve UFO's original missing/corrupt-image fallback behavior.
        from ufo import utils

        original = getattr(utils, "_loom_original_encode_image_from_path", None)
        if callable(original):
            return original(image_path, _mime_type)
        raise


def _install_image_encoding_patch(profile: str) -> None:
    global _CAPTURE_PROFILE
    _CAPTURE_PROFILE = profile

    from ufo import utils
    from ufo.automator.ui_control.screenshot import PhotographerFacade

    if not hasattr(utils, "_loom_original_encode_image"):
        utils._loom_original_encode_image = utils.encode_image
        utils._loom_original_encode_image_from_path = utils.encode_image_from_path
    utils.encode_image = lambda image, mime_type=None: _encode_image_for_model(image)
    utils.encode_image_from_path = _encode_path_for_model

    if not hasattr(PhotographerFacade, "_loom_original_encode_image"):
        PhotographerFacade._loom_original_encode_image = PhotographerFacade.encode_image
        PhotographerFacade._loom_original_encode_image_from_path = PhotographerFacade.encode_image_from_path
    PhotographerFacade.encode_image = classmethod(
        lambda cls, image, mime_type=None: _encode_image_for_model(image)
    )
    PhotographerFacade.encode_image_from_path = classmethod(
        lambda cls, image_path, mime_type=None: _encode_path_for_model(image_path, mime_type)
    )


def _visible_windows(limit: int = _MAX_WINDOW_CONTEXT) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    import psutil
    import win32gui
    import win32process

    windows: list[dict[str, Any]] = []

    def callback(hwnd: int, _extra: object) -> None:
        if len(windows) >= limit:
            return
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                process_name = str(psutil.Process(pid).name() or "")
            except Exception:
                process_name = ""
            windows.append(
                {
                    "handle": int(hwnd),
                    "title": title,
                    "process_name": process_name,
                    "process_id": int(pid),
                    "minimized": bool(win32gui.IsIconic(hwnd)),
                }
            )
        except Exception:
            return

    win32gui.EnumWindows(callback, None)
    return windows


def _choose_focus_candidate(task: str, windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [
        (_window_match_score(task, str(item.get("title") or ""), str(item.get("process_name") or "")), item)
        for item in windows
    ]
    scored = [(score, item) for score, item in scored if score >= 90]
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    if len(scored) > 1 and scored[1][0] == best_score:
        return None
    return best


def _activate_window(window: dict[str, Any]) -> bool:
    if os.name != "nt":
        return False
    import win32con
    import win32gui

    hwnd = int(window.get("handle") or 0)
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        return int(win32gui.GetForegroundWindow()) == hwnd
    except Exception:
        return False


def _desktop_context(task: str, windows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in windows[:_MAX_WINDOW_CONTEXT]:
        lines.append(
            f"- handle=0x{int(item.get('handle') or 0):x}; "
            f"title={str(item.get('title') or '')!r}; "
            f"process={str(item.get('process_name') or '')!r}; "
            f"minimized={bool(item.get('minimized'))}"
        )
    listing = "\n".join(lines) if lines else "- (no titled visible top-level windows)"
    return (
        "\n\n[Loom desktop context]\n"
        "Existing top-level windows before this task:\n"
        f"{listing}\n"
        "Rules for this task: reuse an existing target application when it is listed; "
        "do not press Win+D, hunt for a desktop icon, or relaunch an app merely to bring "
        "it forward. Prefer get_desktop_app_info + select_application_window. After an "
        "action opens a dialog or another top-level window, refresh/select the current "
        "window before the next click. Prefer named UI controls over coordinate clicks "
        "when both are available."
    )


def _control_label_at(application: Any, x_fraction: float, y_fraction: float) -> str:
    try:
        rect = application.rectangle()
        screen_x = int(rect.left + rect.width() * x_fraction)
        screen_y = int(rect.top + rect.height() * y_fraction)
        from ufo.client.mcp.local_servers.ui_mcp_server import UIServerState

        controls = (UIServerState().control_dict or {}).values()
        matches: list[tuple[int, Any]] = []
        for control in controls:
            try:
                target = control.rectangle()
                if target.left <= screen_x < target.right and target.top <= screen_y < target.bottom:
                    area = max(1, target.width()) * max(1, target.height())
                    matches.append((area, control))
            except Exception:
                continue
        if not matches:
            return ""
        _area, control = min(matches, key=lambda item: item[0])
        name = str(getattr(control.element_info, "name", "") or "").strip()
        kind = str(getattr(control.element_info, "control_type", "") or "control").strip()
        return f"{kind} {name!r}" if name else kind
    except Exception:
        return ""


def _install_click_feedback_patch() -> None:
    from ufo.automator.ui_control.controller import ControlReceiver

    if hasattr(ControlReceiver, "_loom_original_click_on_coordinates"):
        return
    original = ControlReceiver.click_on_coordinates

    def patched(self, params: dict[str, Any]) -> str:
        try:
            x = float(params.get("x", 0))
            y = float(params.get("y", 0))
            label = _control_label_at(self.application, x, y)
        except Exception:
            label = ""
        result = original(self, params)
        if label:
            return f"{result} Resolved UI target before click: {label}."
        return result

    ControlReceiver._loom_original_click_on_coordinates = original
    ControlReceiver.click_on_coordinates = patched


def _same_process_foreground(selected_window: dict[str, Any]) -> dict[str, Any] | None:
    if os.name != "nt" or not selected_window:
        return None
    try:
        import win32gui
        import win32process

        old_handle = int(selected_window.get("handle") or 0)
        old_pid = int(selected_window.get("process_id") or 0)
        hwnd = int(win32gui.GetForegroundWindow() or 0)
        if not hwnd or hwnd == old_handle or not win32gui.IsWindow(hwnd):
            return None
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not old_pid or int(pid) != old_pid:
            return None
        title = str(win32gui.GetWindowText(hwnd) or "").strip()
        if not title:
            return None
        left, top, right, bottom = map(int, win32gui.GetWindowRect(hwnd))
        return {
            "handle": hwnd,
            "process_id": int(pid),
            "title": title,
            "name": title,
            "rectangle": {
                "x": left,
                "y": top,
                "width": max(0, right - left),
                "height": max(0, bottom - top),
            },
        }
    except Exception:
        return None


def _reanchor_ufo_window(window_info: dict[str, Any]) -> bool:
    try:
        from pywinauto import Desktop
        from ufo.client.mcp.local_servers.ui_mcp_server import UIServerState

        hwnd = int(window_info.get("handle") or 0)
        wrapper = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        state = UIServerState()
        state.selected_app_window_controls = None
        state.control_dict = None
        state.initialize_for_window(wrapper)
        return True
    except Exception:
        return False


def _install_reanchor_patch() -> None:
    if hasattr(core.TaskController, "_loom_original_after_commands"):
        return
    original = core.TaskController.after_commands

    async def patched(self, commands: list[Any], results: list[Any] | None) -> None:
        await original(self, commands, results)
        had_mutation = False
        result_list = list(results or [])
        for index, command in enumerate(commands):
            if str(getattr(command, "tool_type", "") or "") != "action":
                continue
            name = str(getattr(command, "tool_name", "") or "")
            if name in core._NON_MUTATING_ACTIONS:
                continue
            result = result_list[index] if index < len(result_list) else None
            if core._result_status(result).get("ok"):
                had_mutation = True
                break
        if not had_mutation:
            return
        new_window = _same_process_foreground(dict(self.selected_window))
        if new_window is None or not _reanchor_ufo_window(new_window):
            return
        self.selected_window = new_window
        await self.event(
            "window.reanchored",
            {"window": dict(new_window), "reason": "same_process_foreground_changed"},
        )

    core.TaskController._loom_original_after_commands = original
    core.TaskController.after_commands = patched


async def _patched_run_task(root: Path, request: dict[str, Any], controller: Any) -> None:
    task = str(request.get("task") or "").strip()
    windows = _visible_windows()
    candidate = _choose_focus_candidate(task, windows)
    focused = bool(candidate and _activate_window(candidate))
    if candidate is not None:
        await controller.event(
            "window.prefocus",
            {
                "matched": True,
                "focused": focused,
                "window": {
                    "handle": int(candidate.get("handle") or 0),
                    "title": str(candidate.get("title") or ""),
                    "process_name": str(candidate.get("process_name") or ""),
                    "process_id": int(candidate.get("process_id") or 0),
                },
            },
        )
    enriched = dict(request)
    enriched["task"] = task + _desktop_context(task, windows)
    await _ORIGINAL_RUN_TASK(root, enriched, controller)


def _patched_bootstrap(root: Path) -> dict[str, Any]:
    global _PATCHED
    bootstrap = _ORIGINAL_BOOTSTRAP(root)
    if not _PATCHED:
        profile = _resolve_capture_profile(root)
        _install_image_encoding_patch(profile)
        _install_click_feedback_patch()
        _install_reanchor_patch()
        _PATCHED = True
    bootstrap["loom_runtime_patches"] = {
        "capture_profile": _CAPTURE_PROFILE,
        "model_image_encoding": str(CAPTURE_PROFILES[_CAPTURE_PROFILE]["media_type"]),
        "window_prefocus": True,
        "same_process_reanchor": True,
        "coordinate_target_feedback": True,
    }
    return bootstrap


core._bootstrap_ufo = _patched_bootstrap
core._run_task = _patched_run_task


if __name__ == "__main__":
    raise SystemExit(core.main())
