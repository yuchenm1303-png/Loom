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
import re
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
def _claim_protocol_stdout() -> None:
    """Take exclusive ownership of stdout for the NDJSON protocol.

    contextlib.redirect_stdout only rebinds sys.stdout, so it cannot stop UFO's
    own logging, a third-party library, or a C extension from writing to file
    descriptor 1 and corrupting the protocol stream. Duplicating the descriptor
    and pointing fd 1 at stderr means anything that writes to "stdout" lands in
    the diagnostic stream, and only emit_sync can reach Loom.

    Called from main() rather than at import time: rebinding a descriptor is a
    process-wide side effect that must not fire just because a test or tool
    imports this module.
    """

    global _PROTOCOL_STDOUT
    try:
        sys.stdout.flush()
        protocol_fd = os.dup(sys.stdout.fileno())
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        _PROTOCOL_STDOUT = os.fdopen(
            protocol_fd, "w", encoding="utf-8", newline="\n", buffering=1
        )
    except (OSError, ValueError, AttributeError):
        # No real descriptors available; keep writing to the inherited stream.
        _PROTOCOL_STDOUT = sys.stdout


_PROTOCOL_STDOUT = sys.stdout
_PROTOCOL_WRITE_LOCK = threading.RLock()
_ACTIVE_CONTROLLER: "TaskController | None" = None
_PATCHED = False
_LLM_PATCHED = False
_PROBE_PATCHED = False
# UFO's entrypoints, imported once on the main thread during startup. See
# _preload_ufo_modules for why this must not happen inside the event loop.
_UFO_MODULES: dict[str, Any] = {}
_SENSITIVE_PARAMETER_KEYS = {
    "content",
    "instruction",
    "message",
    "prompt",
    "request",
    "text",
    "value_text",
}
# Introspection and window focusing are how UFO orients itself; neither changes
# the desktop on the user's behalf, so they must not count as work done.
_NON_MUTATING_ACTIONS = frozenset({"list_tools", "select_application_window"})
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
        self.mutating_actions = 0
        self.started_at = time.monotonic()
        self.last_event_at = self.started_at
        # Heartbeats deliberately do not count as activity here, so diagnostics can
        # still show how long UFO has been inside one opaque step.
        self.last_substantive_at = self.started_at
        self.last_event_kind = ""
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
            if kind != "task.heartbeat":
                self.last_substantive_at = now
                self.last_event_kind = kind
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

    async def heartbeat(self, interval: float) -> None:
        """Prove the event loop is still scheduling while a task runs.

        UFO can legitimately spend a long time inside one model call, so silence
        alone cannot distinguish "slow" from "wedged". A heartbeat that stops only
        when the loop itself stops gives the supervisor an unambiguous signal.
        """

        while True:
            await asyncio.sleep(interval)
            with self._lock:
                last = self.last_event_kind
                since = round((time.monotonic() - self.last_substantive_at) * 1000.0, 3)
            await self.event(
                "task.heartbeat",
                {"last_event_kind": last, "since_last_event_ms": since},
            )

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
                status = _result_status(result)
                if tool_name not in _NON_MUTATING_ACTIONS and status["ok"]:
                    # Counts only actions that could have changed the desktop, so
                    # the outcome can distinguish "did nothing" from "did work but
                    # never declared itself done".
                    self.mutating_actions += 1
                await self.event(
                    "action.completed",
                    {
                        "action": tool_name,
                        "result": status,
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


_REASONING_BLOCK_RE = re.compile(
    r"<\s*(think|thinking|reasoning)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_UNCLOSED_REASONING_RE = re.compile(
    r"^\s*<\s*(?:think|thinking|reasoning)\b[^>]*>",
    re.IGNORECASE,
)


def _strip_reasoning_wrapper(text: str) -> str:
    """Remove inline chain-of-thought blocks from a model response.

    UFO parses agent responses as strict JSON. Reasoning models emit a
    <think>...</think> preamble before that JSON, which makes json.loads fail on
    column 1 and burns all of UFO's retries. Loom lets users point Computer Use at
    whichever chat model they have selected, so this normalization belongs at the
    boundary rather than in a per-model allowlist.
    """

    raw = str(text or "")
    cleaned = _REASONING_BLOCK_RE.sub("", raw)
    if _UNCLOSED_REASONING_RE.match(cleaned):
        # Truncated or streamed-away closing tag: keep only what follows the last
        # closing tag if there is one, otherwise fall back to the first JSON-ish
        # character so a partial preamble cannot shadow a usable payload.
        tail = re.split(r"<\s*/\s*(?:think|thinking|reasoning)\s*>", cleaned, maxsplit=1)
        cleaned = tail[-1] if len(tail) > 1 else cleaned
        if _UNCLOSED_REASONING_RE.match(cleaned):
            start = min(
                (pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0),
                default=-1,
            )
            if start >= 0:
                cleaned = cleaned[start:]
    return cleaned.strip() or raw


def _sanitize_llm_response(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_reasoning_wrapper(value)
    if isinstance(value, list):
        return [_sanitize_llm_response(item) for item in value]
    return value


def _install_structured_output_probe_fallback() -> None:
    """Let UFO fall back to text mode when a provider fakes json_schema support.

    On startup UFO probes the endpoint with response_format=<pydantic model> and
    downgrades to text mode if the provider answers BadRequest. Most
    OpenAI-compatible endpoints instead accept the parameter and ignore it,
    returning prose; the SDK then raises a pydantic ValidationError that UFO does
    not catch, so constructing the service fails and every agent turn dies before
    the first screenshot. Translating that into the BadRequest the probe already
    understands routes it into UFO's own intended fallback.

    Only the probe calls .parse(); live turns use .create(), so this does not
    affect providers that genuinely implement structured output.
    """

    global _PROBE_PATCHED
    if _PROBE_PATCHED:
        return
    import httpx
    import openai
    from openai.resources.beta.chat.completions import Completions as BetaChatCompletions
    from pydantic import ValidationError

    if not hasattr(BetaChatCompletions, "_loom_original_parse"):
        original = BetaChatCompletions.parse

        def _patched_parse(self, *args: Any, **kwargs: Any):
            try:
                return original(self, *args, **kwargs)
            except ValidationError as exc:
                raise openai.BadRequestError(
                    "'response_format' of type 'json_schema' is not supported: the "
                    "endpoint accepted the schema but returned unstructured text.",
                    response=httpx.Response(
                        400,
                        request=httpx.Request(
                            "POST", "https://loom.invalid/structured-output-probe"
                        ),
                    ),
                    body=None,
                ) from exc

        BetaChatCompletions._loom_original_parse = original
        BetaChatCompletions.parse = _patched_parse
    _PROBE_PATCHED = True


def _thinking_override(model: str) -> dict[str, Any] | None:
    """Request a non-reasoning response for a UFO agent turn, when supported.

    A UFO turn is a single structured decision, not an open reasoning problem.
    Long chain-of-thought costs seconds per step and is then discarded, because
    UFO only consumes the JSON that follows it. LOOM_UFO_THINKING=default opts
    back out; the payload shape is provider-specific, so only providers whose
    switch is known are touched.
    """

    mode = str(os.environ.get("LOOM_UFO_THINKING") or "auto").strip().casefold()
    if mode == "default":
        return None
    name = str(model or "").casefold()
    if "minimax" in name:
        return {"thinking": {"type": "disabled"}}
    return None


def _install_llm_response_hook() -> None:
    """Normalize UFO's OpenAI-compatible responses before UFO parses them."""

    global _LLM_PATCHED
    if _LLM_PATCHED:
        return
    from ufo.llm.openai import OpenAIService

    if not hasattr(OpenAIService, "_loom_original_chat_completion"):
        original = OpenAIService.chat_completion

        def _patched_chat_completion(self, *args, **kwargs):
            override = _thinking_override(str(getattr(self, "model", "") or ""))
            if override is not None:
                extra_body = dict(kwargs.get("extra_body") or {})
                extra_body.update(override)
                kwargs["extra_body"] = extra_body
            responses, cost = original(self, *args, **kwargs)
            cleaned = _sanitize_llm_response(responses)
            controller = _ACTIVE_CONTROLLER
            if controller is not None and cleaned != responses:
                controller.event_sync(
                    "llm.response.normalized",
                    {
                        "agent_type": str(getattr(self, "agent_type", "") or ""),
                        "removed_reasoning_wrapper": True,
                    },
                )
            return cleaned, cost

        OpenAIService._loom_original_chat_completion = original
        OpenAIService.chat_completion = _patched_chat_completion
    _LLM_PATCHED = True


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
    _install_structured_output_probe_fallback()
    _install_llm_response_hook()
    return {
        "root": str(root),
        "git_head": _git_head(root),
        "expected_tag": EXPECTED_UFO_TAG,
        "expected_commit": EXPECTED_UFO_COMMIT,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "pid": os.getpid(),
    }


def _round_state(session: Any) -> str:
    """Read the agent's own verdict for the round UFO just finished.

    Loom drives one non-interactive round, so the session-level _finish flag is
    never set and cannot be used. AgentStatus on the round is what the agent
    actually concluded: FINISH, FAIL, ERROR, or a mid-flight state if the loop
    stopped for an external reason such as the step ceiling.
    """

    current_round = getattr(session, "current_round", None)
    state = getattr(current_round, "state", None)
    name = getattr(state, "name", None)
    try:
        return str(name() or "") if callable(name) else str(name or "")
    except Exception:
        return ""


def _outcome_summary(reason: str) -> str:
    if reason == "session_error":
        return "UFO desktop task ended in an error state."
    if reason == "agent_reported_failure":
        return (
            "UFO reported that it could not complete the desktop task. The screen "
            "may hold partial changes."
        )
    if reason == "step_budget_exhausted":
        return (
            "UFO desktop task ran out of steps before finishing. Raise max_steps, or "
            "split the request into smaller desktop tasks."
        )
    if reason == "unconfirmed_partial_progress":
        return (
            "UFO executed desktop actions but never reported the task as done, so "
            "the result is unconfirmed. Check the current screen before retrying: "
            "some of the requested changes may already have been applied."
        )
    if reason == "no_effective_action":
        return (
            "UFO ended without performing any desktop action. The usual cause is "
            "that the configured vision model rejected or failed every request; "
            "check the Computer Use model configuration."
        )
    return "UFO desktop task completed."


def _preload_ufo_modules() -> dict[str, Any]:
    """Import UFO's entrypoints once, on the main thread, before the event loop.

    UFO pulls in numpy/faiss/langchain transitively. Loading those native
    extensions from inside an asyncio callback deadlocks the Windows DLL loader
    against the worker thread asyncio uses for blocking stdin reads: the loader
    lock is held across DllMain while OpenBLAS starts its own threads. Doing the
    import here - single-threaded, before asyncio.run - keeps the loader
    uncontended. It also means the ready handshake reports a sidecar that can
    actually start a task, instead of one that still owes several seconds of
    import work to whichever task arrives first.
    """

    started = time.monotonic()
    from config.config_loader import get_ufo_config
    from ufo.module.sessions.session import Session

    _UFO_MODULES["get_ufo_config"] = get_ufo_config
    _UFO_MODULES["Session"] = Session
    return {
        "preloaded": True,
        "duration_ms": _elapsed_ms(started),
        "get_ufo_config": _module_summary(get_ufo_config),
        "Session": _module_summary(Session),
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
    heartbeat_task = asyncio.create_task(
        controller.heartbeat(_float_env("LOOM_UFO_HEARTBEAT_INTERVAL", 5.0, 1.0, 60.0))
    )
    try:
        await controller.stage("ufo.imports.started")
        preloaded = bool(_UFO_MODULES)
        if not preloaded:
            # Startup preload failed or this module was driven directly. Fall back
            # to importing here so a task still runs, accepting the loader-lock
            # risk that _preload_ufo_modules exists to avoid.
            with contextlib.redirect_stdout(sys.stderr):
                from config.config_loader import get_ufo_config
                from ufo.module.sessions.session import Session

            _UFO_MODULES["get_ufo_config"] = get_ufo_config
            _UFO_MODULES["Session"] = Session
        get_ufo_config = _UFO_MODULES["get_ufo_config"]
        Session = _UFO_MODULES["Session"]
        await controller.stage(
            "ufo.imports.completed",
            preloaded=preloaded,
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
        result_count = len(results or [])
        session_error = bool(session.is_error())
        # A session that ran out of steps, or whose every model call was rejected,
        # still unwinds "cleanly", so Loom cannot treat "the coroutine returned" as
        # success. The agent's own verdict lives on the round state.
        #
        # Note session._finish is NOT that verdict: UFO only sets it for
        # interactive and plan-file sessions. Loom drives a single non-interactive
        # round, so _finish is always False here and keying on it would fail every
        # successful task.
        round_state = _round_state(session)
        steps_used = int(getattr(session, "step", 0) or 0)
        step_ceiling = int(
            max_steps_raw
            if max_steps_raw is not None
            else getattr(getattr(config, "system", None), "max_step", 0) or 0
        )
        mutating_actions = int(controller.mutating_actions)
        if session_error or round_state == "ERROR":
            reason = "session_error"
        elif round_state == "FINISH":
            reason = ""
        elif round_state == "FAIL":
            reason = "agent_reported_failure"
        elif step_ceiling and steps_used >= step_ceiling:
            reason = "step_budget_exhausted"
        elif mutating_actions == 0:
            reason = "no_effective_action"
        else:
            # Acted, but the round ended in some other state. Loom must not claim
            # success, and must not imply nothing happened either: a blind retry
            # would repeat the actions that already landed.
            reason = "unconfirmed_partial_progress"
        failed = bool(reason)
        await controller.stage(
            "ufo.session.run.completed",
            result_count=result_count,
            session_error=session_error,
            round_state=round_state,
            mutating_actions=mutating_actions,
            steps_used=steps_used,
            step_ceiling=step_ceiling,
            outcome_reason=reason,
            first_progress_kind=controller.first_progress_kind,
        )

        status = "failed" if failed else "completed"
        await controller.event(
            f"task.{status}",
            {
                "engine": "ufo2",
                "result_count": result_count,
                "outcome_reason": reason,
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
                "summary": _outcome_summary(reason),
                "data": {
                    "engine": "ufo2",
                    "event_count": controller.sequence,
                    "result_count": result_count,
                    "outcome_reason": reason,
                    "mutating_actions": mutating_actions,
                    "steps_used": steps_used,
                    "step_ceiling": step_ceiling,
                    "round_state": round_state,
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
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await heartbeat_task
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


async def main_async(root: Path, bootstrap: dict[str, Any]) -> int:
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
    root = Path(args.ufo_root)
    _claim_protocol_stdout()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            bootstrap = _bootstrap_ufo(root)
            bootstrap["imports"] = _preload_ufo_modules()
    except Exception as exc:
        emit_sync(
            {
                "type": "error",
                "request_id": "",
                "error_type": type(exc).__name__,
                "stage": "startup",
            }
        )
        traceback.print_exc(file=sys.stderr)
        return 1
    return asyncio.run(main_async(root, bootstrap))


if __name__ == "__main__":
    raise SystemExit(main())
