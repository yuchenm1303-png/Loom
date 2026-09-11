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
import tempfile
import threading
import time
import traceback
import uuid
from typing import Any


PROTOCOL = "loom-ufo-sidecar"
PROTOCOL_VERSION = 1
EXPECTED_UFO_TAG = "v3.0.8"
EXPECTED_UFO_COMMIT = "96983c73ed09e884a5f1d7ff8936c953b234b684"
_PROTOCOL_STDOUT = sys.stdout
_PROTOCOL_WRITE_LOCK = threading.RLock()
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
_SCRATCH_PREFIX = "loom-ufo-private-"
_SCRATCH_STALE_SECONDS = 24 * 60 * 60
_FIRST_STEP_PROGRESS_EVENTS = {
    # Early sidecar lifecycle: emitted BEFORE the heavy Session import so the
    # watchdog stops counting down once the task is accepted and UFO is alive.
    "task.started",
    "task.accepted",
    "ufo.imports.started",
    "ufo.imports.completed",
    # Dispatcher-level first-actionable events.
    "dispatcher.commands.started",
    "window.selected",
    "observation.completed",
    "action.started",
    "task.completed",
    "task.failed",
    "task.cancelled",
}


class UfoFirstStepTimeout(TimeoutError):
    """Raised when UFO accepts a task but never emits its first actionable event."""


class _NullWriter:
    """UFO logger-compatible sink used when durable raw logs are disabled."""

    file_path = ""
    mode = "a"

    def write(self, _message: str) -> None:
        return None


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 3)


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _string_summary(value: str) -> dict[str, Any]:
    return {"length": len(str(value or "")), "empty": not bool(str(value or "").strip())}


def _module_summary(value: Any) -> dict[str, str]:
    return {
        "module": str(getattr(value, "__module__", "") or ""),
        "name": str(getattr(value, "__name__", type(value).__name__) or ""),
    }


def emit_sync(message: dict[str, Any]) -> None:
    payload = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
    with _PROTOCOL_WRITE_LOCK:
        _PROTOCOL_STDOUT.write(payload + "\n")
        _PROTOCOL_STDOUT.flush()


async def emit(message: dict[str, Any]) -> None:
    emit_sync(message)


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


def _safe_parameter_value(
    tool_name: str,
    key: str,
    value: Any,
) -> tuple[Any, int | None]:
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


def _safe_command(command: Any) -> dict[str, Any]:
    tool_name = str(getattr(command, "tool_name", "") or "")
    tool_type = str(getattr(command, "tool_type", "") or "")
    raw_parameters = getattr(command, "parameters", {})
    parameters = _safe_parameters(tool_name, raw_parameters)
    return {
        "tool_name": tool_name,
        "tool_type": tool_type,
        "parameter_keys": sorted(parameters.keys()),
        "parameters": parameters,
    }


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


def _hud_point(
    window: dict[str, Any],
    tool_name: str,
    parameters: dict[str, Any],
) -> dict[str, float] | None:
    rect = _rectangle_dict(
        window.get("rectangle") if isinstance(window, dict) else None
    )
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
            point = (
                float(parameters.get("end_x")),
                float(parameters.get("end_y")),
            )
        except (TypeError, ValueError):
            point = None

    if point is None:
        return None
    screen_x = rect["x"] + rect["width"] * point[0]
    screen_y = rect["y"] + rect["height"] * point[1]
    virtual = _virtual_screen_bounds()
    return {
        "x_norm": max(
            0.0,
            min(1.0, (screen_x - virtual["x"]) / virtual["width"]),
        ),
        "y_norm": max(
            0.0,
            min(1.0, (screen_y - virtual["y"]) / virtual["height"]),
        ),
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
            "x_norm": max(
                0.0,
                min(1.0, (screen_x - virtual["x"]) / virtual["width"]),
            ),
            "y_norm": max(
                0.0,
                min(1.0, (screen_y - virtual["y"]) / virtual["height"]),
            ),
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
        self.started_at = time.monotonic()
        self.last_event_at = self.started_at
        self.first_progress_kind = ""
        self.first_progress_at = 0.0
        self._lock = threading.RLock()

    def event_sync(self, kind: str, data: dict[str, Any] | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            payload = dict(data or {})
            payload.setdefault("elapsed_ms", _elapsed_ms(self.started_at))
            payload.setdefault("since_previous_ms", round((now - self.last_event_at) * 1000.0, 3))
            if kind in _FIRST_STEP_PROGRESS_EVENTS and not self.first_progress_kind:
                self.first_progress_kind = kind
                self.first_progress_at = now
                payload["first_progress"] = True
            self.last_event_at = now
            self.sequence += 1
            sequence = self.sequence
        emit_sync(
            {
                "type": "event",
                "request_id": self.request_id,
                "task_id": self.task_id,
                "sequence": sequence,
                "kind": kind,
                "data": payload,
            }
        )

    async def event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        self.event_sync(kind, data)

    async def stage(self, kind: str, **data: Any) -> None:
        await self.event(kind, data)

    def has_first_progress(self) -> bool:
        with self._lock:
            return bool(self.first_progress_kind)

    async def before_commands(self, commands: list[Any]) -> None:
        while self.paused and not self.cancelled:
            await asyncio.sleep(0.1)
        if self.cancelled:
            raise asyncio.CancelledError(self.cancel_reason or "task cancelled")

        safe_commands = [_safe_command(command) for command in commands]
        await self.event(
            "dispatcher.commands.started",
            {
                "command_count": len(safe_commands),
                "commands": safe_commands,
            },
        )

        for command in commands:
            tool_name = str(getattr(command, "tool_name", "") or "")
            tool_type = str(getattr(command, "tool_type", "") or "")
            params = _safe_parameters(
                tool_name,
                getattr(command, "parameters", {}),
            )
            if tool_type != "action":
                continue
            hud = _hud_point(self.selected_window, tool_name, params)
            if hud is None and tool_name in {
                "click_input",
                "set_edit_text",
                "keyboard_input",
            }:
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

    async def after_commands(
        self,
        commands: list[Any],
        results: list[Any] | None,
    ) -> None:
        results = list(results or [])
        safe_results = []
        for index, command in enumerate(commands):
            result = results[index] if index < len(results) else None
            safe_results.append(
                {
                    "command": _safe_command(command),
                    "result": _result_status(result),
                }
            )
        await self.event(
            "dispatcher.commands.completed",
            {
                "command_count": len(commands),
                "results": safe_results,
            },
        )

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
    command_list = list(commands)
    started = time.monotonic()
    if controller is not None:
        await controller.before_commands(command_list)
    try:
        results = await original(self, command_list, timeout=timeout)
    except Exception:
        if controller is not None:
            await controller.event(
                "dispatcher.commands.failed",
                {
                    "command_count": len(command_list),
                    "duration_ms": _elapsed_ms(started),
                    "commands": [_safe_command(command) for command in command_list],
                    "error_type": "DispatcherCommandError",
                },
            )
        raise
    if controller is not None:
        await controller.event(
            "dispatcher.commands.returned",
            {"command_count": len(command_list), "duration_ms": _elapsed_ms(started)},
        )
        await controller.after_commands(command_list, results)
    return results


def _install_dispatcher_hook() -> None:
    global _PATCHED
    if _PATCHED:
        return
    from ufo.module.dispatcher import LocalCommandDispatcher

    if not hasattr(LocalCommandDispatcher, "_loom_original_execute_commands"):
        LocalCommandDispatcher._loom_original_execute_commands = (
            LocalCommandDispatcher.execute_commands
        )
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


def _keep_raw_logs() -> bool:
    return str(os.environ.get("LOOM_UFO_KEEP_RAW_LOGS") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cleanup_stale_scratch() -> None:
    root = Path(tempfile.gettempdir())
    cutoff = time.time() - _SCRATCH_STALE_SECONDS
    try:
        candidates = list(root.glob(f"{_SCRATCH_PREFIX}*"))
    except Exception:
        return
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            if candidate.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(candidate, ignore_errors=True)
        except Exception:
            continue


def _bootstrap_ufo(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not (root / "ufo").is_dir() or not (root / "config" / "ufo").is_dir():
        raise RuntimeError(f"UFO source root is invalid: {root}")
    os.chdir(root)
    sys.path.insert(0, str(root))
    _cleanup_stale_scratch()
    _install_dispatcher_hook()
    return {
        "root": str(root),
        "git_head": _git_head(root),
        "expected_tag": EXPECTED_UFO_TAG,
        "expected_commit": EXPECTED_UFO_COMMIT,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "pid": os.getpid(),
    }


def _cleanup_task_logs(root: Path, task_name: str) -> None:
    if _keep_raw_logs():
        return
    target = (root / "logs" / task_name).resolve()
    logs_root = (root / "logs").resolve()
    try:
        target.relative_to(logs_root)
    except ValueError:
        return
    shutil.rmtree(target, ignore_errors=True)


def _bind_ephemeral_session_logs(session: Any) -> Path | None:
    """Move UFO screenshots to a disposable scratch directory and drop text logs.

    UFO v3.0.8 creates request/response/evaluation logs and saves application
    screenshots under ``logs/<task>`` even when PRINT_LOG/LOG_TO_MARKDOWN are off.
    Loom keeps those artifacts only as transient working state by default. Setting
    LOOM_UFO_KEEP_RAW_LOGS explicitly opts back into upstream UFO persistence.
    """

    if _keep_raw_logs():
        return None

    from ufo.module.context import ContextNames

    scratch = Path(tempfile.mkdtemp(prefix=_SCRATCH_PREFIX)).resolve()
    log_path = str(scratch) + os.sep
    session.log_path = log_path
    session.context.set(ContextNames.LOG_PATH, log_path)
    sink = _NullWriter()
    session.context.set(ContextNames.LOGGER, sink)
    session.context.set(ContextNames.REQUEST_LOGGER, sink)
    session.context.set(ContextNames.EVALUATION_LOGGER, sink)
    return scratch


def _hard_first_step_timeout(controller: TaskController, timeout_seconds: float) -> None:
    if controller.has_first_progress():
        return
    controller.event_sync(
        "task.first_step_timeout",
        {
            "timeout_seconds": timeout_seconds,
            "first_progress_kind": controller.first_progress_kind,
            "last_event_sequence": controller.sequence,
            "hint": (
                "UFO accepted the task but the Python sidecar thread did not observe any "
                "command/window/observation/action event before the timeout. The sidecar "
                "will exit so Loom can surface a deterministic failure instead of hanging."
            ),
        },
    )
    time.sleep(0.25)
    os._exit(124)


async def _watch_first_progress(
    controller: TaskController,
    session_task: asyncio.Task[Any],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not session_task.done() and not controller.has_first_progress():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await controller.event(
                "task.first_step_timeout",
                {
                    "timeout_seconds": timeout_seconds,
                    "first_progress_kind": controller.first_progress_kind,
                    "last_event_sequence": controller.sequence,
                    "last_event_elapsed_ms": _elapsed_ms(controller.started_at),
                    "hint": (
                        "UFO accepted the task but did not dispatch commands, select a window, "
                        "capture an observation, start an action, or return failure/completion."
                    ),
                },
            )
            session_task.cancel()
            raise UfoFirstStepTimeout(
                "UFO first step timeout before any command/window/observation/action event"
            )
        await asyncio.sleep(min(1.0, max(0.05, remaining)))


async def _run_task(
    root: Path,
    request: dict[str, Any],
    controller: TaskController,
) -> None:
    global _ACTIVE_CONTROLLER
    request_id = controller.request_id
    task_id = controller.task_id
    task = str(request.get("task") or "").strip()
    stop_when = str(request.get("stop_when") or "").strip()
    max_steps_raw = request.get("max_steps")
    if not task:
        await emit(
            {
                "type": "error",
                "request_id": request_id,
                "task_id": task_id,
                "error_type": "InvalidTask",
            }
        )
        return

    task_name = f"loom_{task_id.replace('-', '')[:20]}"
    request_text = task
    if stop_when:
        request_text += f"\n\nStop condition: {stop_when}"

    _ACTIVE_CONTROLLER = controller
    await controller.event(
        "task.started",
        {"engine": "ufo2", "task_name": task_name},
    )
    await controller.stage(
        "task.accepted",
        engine="ufo2",
        task_name=task_name,
        task=_string_summary(task),
        stop_when=_string_summary(stop_when),
        max_steps_requested=max_steps_raw,
        raw_logs_persisted=_keep_raw_logs(),
        first_step_timeout_seconds=_float_env("LOOM_UFO_FIRST_STEP_TIMEOUT", 30.0, 5.0, 600.0),
        process={"pid": os.getpid(), "python": sys.version.split()[0], "executable": sys.executable},
        paths={"cwd": os.getcwd(), "ufo_root": str(root)},
        provider={
            "api_type": os.environ.get("LOOM_UFO_API_TYPE", ""),
            "api_base_configured": bool(os.environ.get("LOOM_UFO_API_BASE")),
            "api_key_configured": bool(os.environ.get("LOOM_UFO_API_KEY")),
            "api_model": os.environ.get("LOOM_UFO_API_MODEL", ""),
        },
    )
    previous_max_step = None
    config = None
    scratch_dir: Path | None = None
    hard_timeout_timer: threading.Timer | None = None
    try:
        await controller.stage("ufo.imports.started")
        with contextlib.redirect_stdout(sys.stderr):
            from config.config_loader import get_ufo_config
            from ufo.module.sessions.session import Session
        await controller.stage(
            "ufo.imports.completed",
            get_ufo_config=_module_summary(get_ufo_config),
            Session=_module_summary(Session),
        )

        await controller.stage("ufo.config.load.started")
        with contextlib.redirect_stdout(sys.stderr):
            config = get_ufo_config()
        await controller.stage(
            "ufo.config.load.completed",
            config_class=type(config).__name__,
            system_class=type(getattr(config, "system", None)).__name__,
            original_max_step=getattr(getattr(config, "system", None), "max_step", None),
        )
        if max_steps_raw is not None:
            try:
                max_steps = max(1, min(100, int(max_steps_raw)))
                previous_max_step = config.system.max_step
                config.system.max_step = max_steps
                await controller.stage(
                    "ufo.config.max_step.updated",
                    previous_max_step=previous_max_step,
                    max_step=max_steps,
                )
            except Exception:
                previous_max_step = None
                await controller.stage("ufo.config.max_step.update_failed", error_type="MaxStepUpdateError")

        await controller.stage("ufo.session.create.started")
        with contextlib.redirect_stdout(sys.stderr):
            session = Session(
                task=task_name,
                should_evaluate=False,
                id=task_id,
                request=request_text,
                mode="normal",
            )
        await controller.stage(
            "ufo.session.create.completed",
            session_class=type(session).__name__,
            session_id=str(getattr(session, "id", task_id) or task_id),
            session_log_path=str(getattr(session, "log_path", "") or ""),
        )
        scratch_dir = _bind_ephemeral_session_logs(session)
        await controller.stage(
            "ufo.session.logs.bound",
            raw_logs_persisted=_keep_raw_logs(),
            scratch_dir=(str(scratch_dir) if scratch_dir is not None else ""),
        )
        _cleanup_task_logs(root, task_name)
        await controller.stage("ufo.session.logs.cleaned", task_name=task_name)

        first_step_timeout = _float_env("LOOM_UFO_FIRST_STEP_TIMEOUT", 30.0, 5.0, 600.0)
        await controller.stage("ufo.session.run.started", first_step_timeout_seconds=first_step_timeout)
        hard_timeout_timer = threading.Timer(
            first_step_timeout,
            _hard_first_step_timeout,
            args=(controller, first_step_timeout),
        )
        hard_timeout_timer.daemon = True
        hard_timeout_timer.start()
        session_task = asyncio.create_task(session.run())
        watchdog_task = asyncio.create_task(
            _watch_first_progress(controller, session_task, first_step_timeout)
        )
        done, pending = await asyncio.wait(
            {session_task, watchdog_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for finished in done:
            error = finished.exception()
            if error is not None:
                for item in pending:
                    item.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.gather(*pending)
                raise error
        if watchdog_task in pending:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watchdog_task
        hard_timeout_timer.cancel()
        results = await session_task
        failed = bool(session.is_error())
        await controller.stage(
            "ufo.session.run.completed",
            result_count=len(results or []),
            session_error=failed,
            first_progress_kind=controller.first_progress_kind,
        )

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
                "summary": (
                    "UFO desktop task completed."
                    if not failed
                    else "UFO desktop task ended in an error state."
                ),
                "data": {
                    "engine": "ufo2",
                    "event_count": controller.sequence,
                    "selected_window": dict(controller.selected_window),
                    "raw_logs_persisted": _keep_raw_logs(),
                    "first_progress_kind": controller.first_progress_kind,
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
                "data": {
                    "engine": "ufo2",
                    "event_count": controller.sequence,
                    "raw_logs_persisted": _keep_raw_logs(),
                    "first_progress_kind": controller.first_progress_kind,
                },
            }
        )
    except Exception as exc:
        traceback.print_tb(exc.__traceback__, file=sys.stderr)
        print(
            f"{type(exc).__name__}: [REDACTED_EXCEPTION_MESSAGE]",
            file=sys.stderr,
        )
        error_type = "UFO_FIRST_STEP_TIMEOUT" if isinstance(exc, UfoFirstStepTimeout) else type(exc).__name__
        await controller.event(
            "task.failed",
            {
                "error_type": error_type,
                "first_progress_kind": controller.first_progress_kind,
                "last_event_sequence": controller.sequence,
            },
        )
        await emit(
            {
                "type": "result",
                "request_id": request_id,
                "task_id": task_id,
                "status": "failed",
                "ok": False,
                "summary": f"UFO desktop task failed: {error_type}",
                "data": {
                    "engine": "ufo2",
                    "error_type": error_type,
                    "event_count": controller.sequence,
                    "raw_logs_persisted": _keep_raw_logs(),
                    "first_progress_kind": controller.first_progress_kind,
                },
            }
        )
    finally:
        if hard_timeout_timer is not None:
            hard_timeout_timer.cancel()
        if config is not None and previous_max_step is not None:
            try:
                config.system.max_step = previous_max_step
            except Exception:
                pass
        _ACTIVE_CONTROLLER = None
        if scratch_dir is not None:
            shutil.rmtree(scratch_dir, ignore_errors=True)
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
            await emit(
                {
                    "type": "error",
                    "request_id": "",
                    "error_type": "InvalidJSON",
                }
            )
            continue
        if not isinstance(message, dict):
            await emit(
                {
                    "type": "error",
                    "request_id": "",
                    "error_type": "InvalidCommand",
                }
            )
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
            await emit(
                {"type": "shutdown", "request_id": request_id, "ok": True}
            )
            return 0
        if command == "run_task":
            if active is not None and not active.done():
                await emit(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "error_type": "TaskAlreadyRunning",
                    }
                )
                continue
            task_id = str(message.get("task_id") or uuid.uuid4())
            controller = TaskController(request_id, task_id)
            active = asyncio.create_task(_run_task(root, message, controller))
            continue
        if command == "pause":
            if controller is None or active is None or active.done():
                await emit(
                    {
                        "type": "state",
                        "request_id": request_id,
                        "state": "idle",
                        "ok": False,
                    }
                )
            else:
                controller.paused = True
                await controller.event("task.paused")
                await emit(
                    {
                        "type": "state",
                        "request_id": request_id,
                        "task_id": controller.task_id,
                        "state": "paused",
                        "ok": True,
                    }
                )
            continue
        if command == "resume":
            if controller is None or active is None or active.done():
                await emit(
                    {
                        "type": "state",
                        "request_id": request_id,
                        "state": "idle",
                        "ok": False,
                    }
                )
            else:
                controller.paused = False
                await controller.event("task.resumed")
                await emit(
                    {
                        "type": "state",
                        "request_id": request_id,
                        "task_id": controller.task_id,
                        "state": "running",
                        "ok": True,
                    }
                )
            continue
        if command == "cancel":
            if controller is None or active is None or active.done():
                await emit(
                    {
                        "type": "state",
                        "request_id": request_id,
                        "state": "idle",
                        "ok": False,
                    }
                )
            else:
                controller.cancelled = True
                controller.cancel_reason = str(
                    message.get("reason") or "user_requested"
                )
                active.cancel()
                await emit(
                    {
                        "type": "state",
                        "request_id": request_id,
                        "task_id": controller.task_id,
                        "state": "cancelling",
                        "ok": True,
                    }
                )
            continue

        await emit(
            {
                "type": "error",
                "request_id": request_id,
                "error_type": "UnsupportedCommand",
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ufo-root", required=True)
    args = parser.parse_args()
    return asyncio.run(main_async(Path(args.ufo_root)))


if __name__ == "__main__":
    raise SystemExit(main())
