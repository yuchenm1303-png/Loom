from __future__ import annotations

"""Loom <-> Microsoft UFO local sidecar.

This module is intentionally standalone: it is launched with UFO's isolated
Python environment and imports no Loom package modules. stdout is reserved for
NDJSON protocol messages; UFO's console output is redirected to stderr.
"""

import argparse
import asyncio
import contextlib
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import uuid
from typing import Any


PROTOCOL = "loom-ufo-sidecar"
PROTOCOL_VERSION = 1
EXPECTED_UFO_TAG = "v3.0.8"
EXPECTED_UFO_COMMIT = "96983c73ed09e884a5f1d7ff8936c953b234b684"
_PROTOCOL_STDOUT = sys.stdout
_EMIT_LOCK = asyncio.Lock()
_ACTIVE_CONTROLLER: "TaskController | None" = None
_PATCHED = False
_SENSITIVE_PARAMETER_KEYS = {
    "content",
    "instruction",
    "message",
    "prompt",
    "request",
    "text",
    "value_text",
}


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Path):
        return str(value)
    return str(value)


async def emit(message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    async with _EMIT_LOCK:
        _PROTOCOL_STDOUT.write(payload + "\n")
        _PROTOCOL_STDOUT.flush()


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _text_length(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, default=_json_default))
    except Exception:
        return len(str(value or ""))


def _safe_parameter_value(tool_name: str, key: str, value: Any) -> tuple[Any, int | None]:
    lower_key = str(key or "").casefold()
    lower_name = str(tool_name or "").casefold()
    if lower_key in _SENSITIVE_PARAMETER_KEYS:
        return "[TRANSIENT_TEXT]", _text_length(value)
    if lower_key in {"keys", "clipboard", "clipboard_text"} and any(
        token in lower_name for token in ("type", "text", "keyboard", "clipboard")
    ):
        return "[TRANSIENT_KEYS]", _text_length(value)
    if isinstance(value, dict):
        return _safe_parameters(tool_name, value), None
    if isinstance(value, (list, tuple)):
        scrubbed: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                scrubbed.append(_safe_parameters(tool_name, item))
            else:
                scrubbed.append(item)
        return scrubbed, None
    return value, None


def _safe_parameters(tool_name: str, parameters: Any) -> dict[str, Any]:
    values = _safe_mapping(parameters)
    safe: dict[str, Any] = {}
    for key, value in values.items():
        scrubbed, length = _safe_parameter_value(tool_name, str(key), value)
        safe[str(key)] = scrubbed
        if length is not None:
            safe[f"{key}_length"] = length
    return safe


def _result_status(value: Any) -> dict[str, Any]:
    if value is None:
        return {"status": "unknown", "ok": False, "has_error": False}
    status = getattr(value, "status", None)
    error = getattr(value, "error", None)
    status_text = str(getattr(status, "value", status) or "")
    return {
        "status": status_text or "unknown",
        "ok": status_text.casefold() in {"success", "completed", "ok"},
        "has_error": bool(error),
    }


def _extract_result_payload(value: Any) -> Any:
    raw = getattr(value, "result", None)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except Exception:
                return None
    return raw


def _virtual_screen_bounds() -> dict[str, int]:
    if os.name != "nt":
        return {"x": 0, "y": 0, "width": 1, "height": 1}
    user32 = ctypes.windll.user32
    x = int(user32.GetSystemMetrics(76))
    y = int(user32.GetSystemMetrics(77))
    width = max(1, int(user32.GetSystemMetrics(78)))
    height = max(1, int(user32.GetSystemMetrics(79)))
    return {"x": x, "y": y, "width": width, "height": height}


def _rectangle_dict(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        try:
            return {
                "x": int(value.get("x") or 0),
                "y": int(value.get("y") or 0),
                "width": max(0, int(value.get("width") or 0)),
                "height": max(0, int(value.get("height") or 0)),
            }
        except Exception:
            return {}
    try:
        return {
            "x": int(value.left),
            "y": int(value.top),
            "width": max(0, int(value.width())),
            "height": max(0, int(value.height())),
        }
    except Exception:
        return {}


def _hud_point(window: dict[str, Any], tool_name: str, parameters: dict[str, Any]) -> dict[str, float] | None:
    rect = _rectangle_dict(window.get("rectangle") if isinstance(window, dict) else None)
    if not rect or rect["width"] <= 0 or rect["height"] <= 0:
        return None

    point: tuple[float, float] | None = None
    if tool_name in {"click_on_coordinates", "mouse_move"}:
        try:
            point = (float(parameters.get("x")), float(parameters.get("y")))
        except (TypeError, ValueError):
            point = None
    elif tool_name == "drag_on_coordinates":
        try:
            point = (float(parameters.get("end_x")), float(parameters.get("end_y")))
        except (TypeError, ValueError):
            point = None

    if point is None:
        return None
    screen_x = rect["x"] + rect["width"] * point[0]
    screen_y = rect["y"] + rect["height"] * point[1]
    virtual = _virtual_screen_bounds()
    return {
        "x_norm": max(0.0, min(1.0, (screen_x - virtual["x"]) / virtual["width"])),
        "y_norm": max(0.0, min(1.0, (screen_y - virtual["y"]) / virtual["height"])),
        "screen_x": round(screen_x, 2),
        "screen_y": round(screen_y, 2),
    }


def _uia_hud_point(control_id: str) -> dict[str, float] | None:
    if not control_id:
        return None
    try:
        from ufo.client.mcp.local_servers.ui_mcp_server import UIServerState

        state = UIServerState()
        control_dict = state.control_dict or state.selected_app_window_controls or {}
        control = control_dict.get(str(control_id))
        if control is None:
            return None
        rect = _rectangle_dict(control.rectangle())
        if not rect or rect["width"] <= 0 or rect["height"] <= 0:
            return None
        screen_x = rect["x"] + rect["width"] / 2
        screen_y = rect["y"] + rect["height"] / 2
        virtual = _virtual_screen_bounds()
        return {
            "x_norm": max(0.0, min(1.0, (screen_x - virtual["x"]) / virtual["width"])),
            "y_norm": max(0.0, min(1.0, (screen_y - virtual["y"]) / virtual["height"])),
            "screen_x": round(screen_x, 2),
            "screen_y": round(screen_y, 2),
        }
    except Exception:
        return None


class TaskController:
    def __init__(self, request_id: str, task_id: str) -> None:
        self.request_id = request_id
        self.task_id = task_id
        self.sequence = 0
        self.paused = False
        self.cancelled = False
        self.cancel_reason = ""
        self.selected_window: dict[str, Any] = {}

    async def event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        self.sequence += 1
        await emit(
            {
                "type": "event",
                "request_id": self.request_id,
                "task_id": self.task_id,
                "sequence": self.sequence,
                "kind": kind,
                "data": data or {},
            }
        )

    async def before_commands(self, commands: list[Any]) -> None:
        while self.paused and not self.cancelled:
            await asyncio.sleep(0.1)
        if self.cancelled:
            raise asyncio.CancelledError(self.cancel_reason or "task cancelled")

        for command in commands:
            tool_name = str(getattr(command, "tool_name", "") or "")
            tool_type = str(getattr(command, "tool_type", "") or "")
            params = _safe_parameters(tool_name, getattr(command, "parameters", {}))
            if tool_type != "action":
                continue
            hud = _hud_point(self.selected_window, tool_name, params)
            if hud is None and tool_name in {"click_input", "set_edit_text", "keyboard_input"}:
                hud = _uia_hud_point(str(params.get("id") or ""))
            await self.event(
                "action.started",
                {
                    "action": tool_name,
                    "parameters": params,
                    "window": dict(self.selected_window),
                    "hud_point": hud,
                },
            )

    async def after_commands(self, commands: list[Any], results: list[Any] | None) -> None:
        results = list(results or [])
        for index, command in enumerate(commands):
            tool_name = str(getattr(command, "tool_name", "") or "")
            tool_type = str(getattr(command, "tool_type", "") or "")
            result = results[index] if index < len(results) else None
            if tool_name == "select_application_window":
                payload = _extract_result_payload(result)
                if isinstance(payload, dict):
                    raw_window = payload.get("window_info")
                    if isinstance(raw_window, dict):
                        self.selected_window = dict(raw_window)
                        await self.event(
                            "window.selected",
                            {
                                "window": dict(self.selected_window),
                                "root_name": str(payload.get("root_name") or ""),
                            },
                        )
            if tool_type == "action":
                await self.event(
                    "action.completed",
                    {
                        "action": tool_name,
                        "result": _result_status(result),
                        "window": dict(self.selected_window),
                    },
                )
            elif tool_name in {
                "capture_window_screenshot",
                "get_app_window_controls_info",
                "get_app_window_controls_target_info",
                "get_desktop_app_info",
            }:
                await self.event(
                    "observation.completed",
                    {"operation": tool_name, "result": _result_status(result)},
                )


async def _patched_execute_commands(self, commands, timeout=6000):
    controller = _ACTIVE_CONTROLLER
    original = getattr(type(self), "_loom_original_execute_commands")
    if controller is not None:
        await controller.before_commands(list(commands))
    results = await original(self, commands, timeout=timeout)
    if controller is not None:
        await controller.after_commands(list(commands), results)
    return results


def _install_dispatcher_hook() -> None:
    global _PATCHED
    if _PATCHED:
        return
    from ufo.module.dispatcher import LocalCommandDispatcher

    if not hasattr(LocalCommandDispatcher, "_loom_original_execute_commands"):
        LocalCommandDispatcher._loom_original_execute_commands = LocalCommandDispatcher.execute_commands
        LocalCommandDispatcher.execute_commands = _patched_execute_commands
    _PATCHED = True


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
        return str(result.stdout or "").strip()
    except Exception:
        return ""


def _bootstrap_ufo(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not (root / "ufo").is_dir() or not (root / "config" / "ufo").is_dir():
        raise RuntimeError(f"UFO source root is invalid: {root}")
    os.chdir(root)
    sys.path.insert(0, str(root))
    _install_dispatcher_hook()
    return {
        "root": str(root),
        "git_head": _git_head(root),
        "expected_tag": EXPECTED_UFO_TAG,
        "expected_commit": EXPECTED_UFO_COMMIT,
    }


def _cleanup_task_logs(root: Path, task_name: str) -> None:
    if str(os.environ.get("LOOM_UFO_KEEP_RAW_LOGS") or "").strip().casefold() in {"1", "true", "yes", "on"}:
        return
    target = (root / "logs" / task_name).resolve()
    logs_root = (root / "logs").resolve()
    try:
        target.relative_to(logs_root)
    except ValueError:
        return
    shutil.rmtree(target, ignore_errors=True)


async def _run_task(root: Path, request: dict[str, Any], controller: TaskController) -> None:
    global _ACTIVE_CONTROLLER
    request_id = controller.request_id
    task_id = controller.task_id
    task = str(request.get("task") or "").strip()
    stop_when = str(request.get("stop_when") or "").strip()
    max_steps_raw = request.get("max_steps")
    if not task:
        await emit({"type": "error", "request_id": request_id, "task_id": task_id, "error_type": "InvalidTask"})
        return

    task_name = f"loom_{task_id.replace('-', '')[:20]}"
    request_text = task
    if stop_when:
        request_text += f"\n\nStop condition: {stop_when}"

    _ACTIVE_CONTROLLER = controller
    await controller.event("task.started", {"engine": "ufo2", "task_name": task_name})
    previous_max_step = None
    config = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from config.config_loader import get_ufo_config
            from ufo.module.sessions.session import Session

            config = get_ufo_config()
            if max_steps_raw is not None:
                try:
                    max_steps = max(1, min(100, int(max_steps_raw)))
                    previous_max_step = config.system.max_step
                    config.system.max_step = max_steps
                except Exception:
                    previous_max_step = None

            session = Session(
                task=task_name,
                should_evaluate=False,
                id=task_id,
                request=request_text,
                mode="normal",
            )
            results = await session.run()
            failed = bool(session.is_error())

        status = "failed" if failed else "completed"
        await controller.event(
            f"task.{status}",
            {
                "engine": "ufo2",
                "result_count": len(results or []),
                "selected_window": dict(controller.selected_window),
            },
        )
        await emit(
            {
                "type": "result",
                "request_id": request_id,
                "task_id": task_id,
                "status": status,
                "ok": not failed,
                "summary": "UFO desktop task completed." if not failed else "UFO desktop task ended in an error state.",
                "data": {
                    "engine": "ufo2",
                    "event_count": controller.sequence,
                    "selected_window": dict(controller.selected_window),
                },
            }
        )
    except asyncio.CancelledError:
        controller.cancelled = True
        await controller.event("task.cancelled", {"reason": "cancelled"})
        await emit(
            {
                "type": "result",
                "request_id": request_id,
                "task_id": task_id,
                "status": "cancelled",
                "ok": False,
                "summary": "UFO desktop task was cancelled.",
                "data": {"engine": "ufo2", "event_count": controller.sequence},
            }
        )
    except Exception as exc:
        # UFO/provider exceptions may echo prompts or typed text. Keep frame-level
        # debugging on stderr, but never emit exception messages across the NDJSON
        # boundary or into Loom's durable driver trace.
        traceback.print_tb(exc.__traceback__, file=sys.stderr)
        print(f"{type(exc).__name__}: [REDACTED_EXCEPTION_MESSAGE]", file=sys.stderr)
        error_type = type(exc).__name__
        await controller.event("task.failed", {"error_type": error_type})
        await emit(
            {
                "type": "result",
                "request_id": request_id,
                "task_id": task_id,
                "status": "failed",
                "ok": False,
                "summary": f"UFO desktop task failed: {error_type}",
                "data": {"engine": "ufo2", "error_type": error_type, "event_count": controller.sequence},
            }
        )
    finally:
        if config is not None and previous_max_step is not None:
            try:
                config.system.max_step = previous_max_step
            except Exception:
                pass
        _ACTIVE_CONTROLLER = None
        _cleanup_task_logs(root, task_name)


async def main_async(root: Path) -> int:
    with contextlib.redirect_stdout(sys.stderr):
        bootstrap = _bootstrap_ufo(root)
    await emit(
        {
            "type": "ready",
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            **bootstrap,
        }
    )

    active: asyncio.Task[None] | None = None
    controller: TaskController | None = None

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if line == "":
            if active is not None and not active.done():
                if controller is not None:
                    controller.cancelled = True
                    controller.cancel_reason = "stdin_closed"
                active.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await active
            return 0
        try:
            message = json.loads(line)
        except Exception:
            await emit({"type": "error", "request_id": "", "error_type": "InvalidJSON"})
            continue
        if not isinstance(message, dict):
            await emit({"type": "error", "request_id": "", "error_type": "InvalidCommand"})
            continue

        command = str(message.get("command") or "").strip().casefold()
        request_id = str(message.get("request_id") or uuid.uuid4().hex)
        if command == "hello":
            await emit(
                {
                    "type": "hello",
                    "request_id": request_id,
                    "protocol": PROTOCOL,
                    "protocol_version": PROTOCOL_VERSION,
                    **bootstrap,
                }
            )
            continue
        if command == "shutdown":
            if active is not None and not active.done():
                if controller is not None:
                    controller.cancelled = True
                    controller.cancel_reason = "shutdown"
                active.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await active
            await emit({"type": "shutdown", "request_id": request_id, "ok": True})
            return 0
        if command == "run_task":
            if active is not None and not active.done():
                await emit({"type": "error", "request_id": request_id, "error_type": "TaskAlreadyRunning"})
                continue
            task_id = str(message.get("task_id") or uuid.uuid4())
            controller = TaskController(request_id, task_id)
            active = asyncio.create_task(_run_task(root, message, controller))
            continue
        if command == "pause":
            if controller is None or active is None or active.done():
                await emit({"type": "state", "request_id": request_id, "state": "idle", "ok": False})
            else:
                controller.paused = True
                await controller.event("task.paused")
                await emit({"type": "state", "request_id": request_id, "task_id": controller.task_id, "state": "paused", "ok": True})
            continue
        if command == "resume":
            if controller is None or active is None or active.done():
                await emit({"type": "state", "request_id": request_id, "state": "idle", "ok": False})
            else:
                controller.paused = False
                await controller.event("task.resumed")
                await emit({"type": "state", "request_id": request_id, "task_id": controller.task_id, "state": "running", "ok": True})
            continue
        if command == "cancel":
            if controller is None or active is None or active.done():
                await emit({"type": "state", "request_id": request_id, "state": "idle", "ok": False})
            else:
                controller.cancelled = True
                controller.cancel_reason = str(message.get("reason") or "user_requested")
                active.cancel()
                await emit({"type": "state", "request_id": request_id, "task_id": controller.task_id, "state": "cancelling", "ok": True})
            continue

        await emit({"type": "error", "request_id": request_id, "error_type": "UnsupportedCommand"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ufo-root", required=True)
    args = parser.parse_args()
    return asyncio.run(main_async(Path(args.ufo_root)))


if __name__ == "__main__":
    raise SystemExit(main())
