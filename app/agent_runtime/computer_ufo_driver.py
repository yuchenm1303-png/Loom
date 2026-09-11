from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from typing import Any, Mapping

from .computer_driver import (
    ComputerDriverCancelCheck,
    ComputerDriverEvent,
    ComputerDriverEventListener,
    ComputerDriverResult,
    ComputerDriverUnavailableError,
)


UFO_VERSION = "3.0.8"
UFO_TAG = "v3.0.8"
UFO_COMMIT = "96983c73ed09e884a5f1d7ff8936c953b234b684"
PROTOCOL = "loom-ufo-sidecar"
PROTOCOL_VERSION = 1


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _runtime_home() -> Path:
    return Path(_first_env("LOOM_HOME") or (Path.home() / ".loom")).expanduser().resolve()


def _default_install_root() -> Path:
    return _runtime_home() / "drivers" / "ufo" / UFO_VERSION


def _default_source_root(install_root: Path) -> Path:
    return install_root / "src"


def _default_python(install_root: Path) -> Path:
    if os.name == "nt":
        return install_root / ".venv" / "Scripts" / "python.exe"
    return install_root / ".venv" / "bin" / "python"


def _sidecar_path() -> Path:
    return Path(__file__).with_name("ufo_sidecar.py").resolve()


def _normalize_base_url(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base.casefold().endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    return base


@dataclass(frozen=True, slots=True)
class UfoDriverConfig:
    install_root: Path
    source_root: Path
    python: Path
    sidecar: Path
    api_type: str
    api_base: str
    api_key: str
    api_model: str
    strict: bool = False
    startup_timeout_seconds: float = 45.0
    cancel_timeout_seconds: float = 2.0

    @classmethod
    def from_environment(cls, *, strict: bool = False) -> "UfoDriverConfig":
        install_root = Path(_first_env("LOOM_UFO_INSTALL_ROOT") or _default_install_root()).expanduser().resolve()
        source_root = Path(_first_env("LOOM_UFO_ROOT") or _default_source_root(install_root)).expanduser().resolve()
        python = Path(_first_env("LOOM_UFO_PYTHON") or _default_python(install_root)).expanduser().resolve()
        sidecar = Path(_first_env("LOOM_UFO_SIDECAR") or _sidecar_path()).expanduser().resolve()

        explicit_type = _first_env("LOOM_UFO_API_TYPE").casefold()
        explicit_base = _first_env("LOOM_UFO_API_BASE", "LOOM_BASE_URL")
        explicit_key = _first_env("LOOM_UFO_API_KEY")
        explicit_model = _first_env("LOOM_UFO_API_MODEL")

        if explicit_key:
            api_key = explicit_key
            api_model = explicit_model or _first_env("LOOM_MODEL")
            api_base = _normalize_base_url(explicit_base)
            api_type = explicit_type or "openai"
        elif _first_env("OPENAI_API_KEY"):
            api_key = _first_env("OPENAI_API_KEY")
            api_model = explicit_model or _first_env("LOOM_MODEL")
            api_base = _normalize_base_url(explicit_base or "https://api.openai.com/v1")
            api_type = explicit_type or "openai"
        elif _first_env("DASHSCOPE_API_KEY"):
            api_key = _first_env("DASHSCOPE_API_KEY")
            api_model = explicit_model or "qwen-vl-max"
            api_base = _normalize_base_url(
                explicit_base or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            api_type = explicit_type or "openai"
        else:
            api_key = _first_env("LOOM_API_KEY", "AI_API_KEY")
            api_model = explicit_model or _first_env("LOOM_MODEL", "AGENT_MODEL")
            api_base = _normalize_base_url(explicit_base)
            api_type = explicit_type or "openai"

        return cls(
            install_root=install_root,
            source_root=source_root,
            python=python,
            sidecar=sidecar,
            api_type=api_type,
            api_base=api_base,
            api_key=api_key,
            api_model=api_model,
            strict=bool(strict),
            startup_timeout_seconds=float(_first_env("LOOM_UFO_STARTUP_TIMEOUT") or 45.0),
            cancel_timeout_seconds=float(_first_env("LOOM_UFO_CANCEL_TIMEOUT") or 2.0),
        )

    def readiness(self) -> tuple[bool, str]:
        if os.name != "nt":
            return False, "UFO Windows driver requires Windows"
        if not self.sidecar.is_file():
            return False, f"Loom UFO sidecar is missing: {self.sidecar}"
        if not self.source_root.is_dir() or not (self.source_root / "ufo").is_dir():
            return False, f"UFO {UFO_TAG} is not installed; run npm run setup:ufo"
        if not self.python.is_file():
            return False, f"UFO isolated Python is missing; run npm run setup:ufo"
        if not (self.source_root / "config" / "ufo" / "agents.yaml").is_file():
            return False, "UFO Loom agent configuration is missing; run npm run setup:ufo"
        if not (self.source_root / "config" / "ufo" / "system_loom.yaml").is_file():
            return False, "UFO Loom safety override is missing; run npm run setup:ufo"
        if not (self.source_root / "config" / "ufo" / "mcp_loom.yaml").is_file():
            return False, "UFO Loom MCP allowlist is missing; run npm run setup:ufo"
        if not self.api_key:
            return False, "UFO model API key is not configured (set LOOM_UFO_API_KEY or a supported provider key)"
        if not self.api_model:
            return False, "UFO vision model is not configured (set LOOM_UFO_API_MODEL)"
        if not self.api_base and self.api_type == "openai":
            return False, "UFO OpenAI-compatible API base is not configured"
        return True, ""

    def process_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "UFO_ENV": "loom",
                "LOOM_UFO_API_TYPE": self.api_type,
                "LOOM_UFO_API_BASE": self.api_base,
                "LOOM_UFO_API_KEY": self.api_key,
                "LOOM_UFO_API_MODEL": self.api_model,
            }
        )
        return env


class UfoWindowsDriver:
    """Microsoft UFO² v3.0.8 isolated sidecar driver.

    The driver deliberately does not import UFO into Loom's main interpreter.
    UFO has its own pinned dependency graph, so a separate environment and an
    NDJSON process boundary keep Loom's Agent Runtime stable and make hard
    cancellation possible without killing Loom Desktop.
    """

    name = "ufo2-sidecar"

    def __init__(self, config: UfoDriverConfig | None = None) -> None:
        self.config = config or UfoDriverConfig.from_environment()
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=100)
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._write_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._active_task_id = ""
        self._paused = False
        self._verified_commit = ""

    @classmethod
    def from_environment(cls, *, strict: bool = False) -> "UfoWindowsDriver":
        return cls(UfoDriverConfig.from_environment(strict=strict))

    @property
    def ready(self) -> bool:
        ready, _ = self.config.readiness()
        return ready

    def status(self) -> Mapping[str, Any]:
        ready, reason = self.config.readiness()
        process = self._process
        alive = bool(process is not None and process.poll() is None)
        return {
            "name": self.name,
            "engine": "microsoft-ufo2",
            "version": UFO_VERSION,
            "tag": UFO_TAG,
            "expected_commit": UFO_COMMIT,
            "verified_commit": self._verified_commit,
            "installed": self.config.source_root.is_dir(),
            "ready": ready,
            "reason": reason,
            "sidecar_alive": alive,
            "running": bool(self._active_task_id),
            "active_task_id": self._active_task_id,
            "paused": self._paused,
            "source_root": str(self.config.source_root),
            "python": str(self.config.python),
            "api_type": self.config.api_type,
            "api_base_configured": bool(self.config.api_base),
            "api_key_configured": bool(self.config.api_key),
            "api_model": self.config.api_model,
            "transport": "stdio-ndjson",
            "network_listener": False,
            "strict": self.config.strict,
        }

    def _creationflags(self) -> int:
        if os.name != "nt":
            return 0
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)

    def _stdout_loop(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            return
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                payload = {"type": "protocol_error", "error": "non-JSON sidecar stdout", "preview": line[:500]}
            if isinstance(payload, dict):
                self._messages.put(payload)

    def _stderr_loop(self, process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip()
            if line:
                self._stderr.append(line[-2000:])

    def _hard_stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except Exception:
            pass
        with self._state_lock:
            self._active_task_id = ""
            self._paused = False

    def _start(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            return
        self._hard_stop()
        ready, reason = self.config.readiness()
        if not ready:
            raise ComputerDriverUnavailableError(reason)

        argv = [
            str(self.config.python),
            str(self.config.sidecar),
            "--ufo-root",
            str(self.config.source_root),
        ]
        process = subprocess.Popen(
            argv,
            cwd=str(self.config.source_root),
            env=self.config.process_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=self._creationflags(),
        )
        self._process = process
        self._reader = threading.Thread(target=self._stdout_loop, args=(process,), daemon=True, name="loom-ufo-stdout")
        self._stderr_reader = threading.Thread(target=self._stderr_loop, args=(process,), daemon=True, name="loom-ufo-stderr")
        self._reader.start()
        self._stderr_reader.start()

        deadline = time.monotonic() + max(5.0, self.config.startup_timeout_seconds)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = "\n".join(list(self._stderr)[-10:])
                self._hard_stop()
                raise ComputerDriverUnavailableError(
                    f"UFO sidecar exited during startup{': ' + detail if detail else ''}"
                )
            try:
                message = self._messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if message.get("type") == "ready":
                if message.get("protocol") != PROTOCOL or int(message.get("protocol_version") or 0) != PROTOCOL_VERSION:
                    self._hard_stop()
                    raise ComputerDriverUnavailableError("UFO sidecar protocol version mismatch")
                head = str(message.get("git_head") or "")
                self._verified_commit = head
                if head and head != UFO_COMMIT:
                    self._hard_stop()
                    raise ComputerDriverUnavailableError(
                        f"UFO source revision mismatch: expected {UFO_COMMIT}, got {head}"
                    )
                return
            if message.get("type") in {"error", "protocol_error"}:
                self._hard_stop()
                raise ComputerDriverUnavailableError(str(message.get("error") or "UFO sidecar startup failed"))
        self._hard_stop()
        raise ComputerDriverUnavailableError("UFO sidecar did not become ready before the startup timeout")

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise ComputerDriverUnavailableError("UFO sidecar is not running")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process.stdin.write(line + "\n")
            process.stdin.flush()

    def _send_control(self, command: str, **extra: Any) -> bool:
        try:
            self._start()
            self._send({"command": command, "request_id": uuid.uuid4().hex, **extra})
            return True
        except Exception:
            return False

    def run_task(
        self,
        task: str,
        *,
        stop_when: str = "",
        max_steps: int | None = None,
        on_event: ComputerDriverEventListener | None = None,
        is_cancelled: ComputerDriverCancelCheck = lambda: False,
    ) -> ComputerDriverResult:
        text = str(task or "").strip()
        if not text:
            raise ValueError("UFO computer task must not be empty")
        if len(text) > 20_000:
            raise ValueError("UFO computer task exceeds 20,000 characters")
        stop = str(stop_when or "").strip()
        if len(stop) > 4_000:
            raise ValueError("UFO computer stop condition exceeds 4,000 characters")

        if not self._run_lock.acquire(blocking=False):
            raise ComputerDriverUnavailableError("another UFO desktop task is already running")
        try:
            self._start()
            task_id = str(uuid.uuid4())
            request_id = uuid.uuid4().hex
            with self._state_lock:
                self._active_task_id = task_id
                self._paused = False
            payload: dict[str, Any] = {
                "command": "run_task",
                "request_id": request_id,
                "task_id": task_id,
                "task": text,
                "stop_when": stop,
            }
            if max_steps is not None:
                payload["max_steps"] = max(1, min(100, int(max_steps)))
            self._send(payload)

            cancel_sent = False
            cancel_deadline = 0.0
            while True:
                process = self._process
                if process is None or process.poll() is not None:
                    detail = "\n".join(list(self._stderr)[-12:])
                    return ComputerDriverResult(
                        task_id=task_id,
                        status="failed",
                        ok=False,
                        summary="UFO sidecar exited before returning a task result.",
                        data={"engine": "ufo2", "stderr_tail": detail[-4000:]},
                    )

                if is_cancelled() and not cancel_sent:
                    cancel_sent = True
                    cancel_deadline = time.monotonic() + max(0.5, self.config.cancel_timeout_seconds)
                    self._send(
                        {
                            "command": "cancel",
                            "request_id": uuid.uuid4().hex,
                            "task_id": task_id,
                            "reason": "loom_turn_cancelled",
                        }
                    )
                if cancel_sent and time.monotonic() >= cancel_deadline:
                    self._hard_stop()
                    return ComputerDriverResult(
                        task_id=task_id,
                        status="cancelled",
                        ok=False,
                        summary="UFO desktop task was hard-cancelled after the cooperative timeout.",
                        data={"engine": "ufo2", "hard_cancelled": True},
                    )

                try:
                    message = self._messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if str(message.get("request_id") or "") != request_id:
                    continue
                message_type = str(message.get("type") or "")
                if message_type == "event":
                    event = ComputerDriverEvent(
                        task_id=task_id,
                        sequence=int(message.get("sequence") or 0),
                        kind=str(message.get("kind") or "event"),
                        data=dict(message.get("data") or {}),
                    )
                    if on_event is not None:
                        on_event(event)
                    continue
                if message_type == "result":
                    return ComputerDriverResult(
                        task_id=task_id,
                        status=str(message.get("status") or "unknown"),
                        ok=bool(message.get("ok")),
                        summary=str(message.get("summary") or ""),
                        data=dict(message.get("data") or {}),
                    )
                if message_type in {"error", "protocol_error"}:
                    return ComputerDriverResult(
                        task_id=task_id,
                        status="failed",
                        ok=False,
                        summary="UFO sidecar protocol task failed.",
                        data={"engine": "ufo2", "error": str(message.get("error") or "unknown error")[:1000]},
                    )
        finally:
            with self._state_lock:
                self._active_task_id = ""
                self._paused = False
            self._run_lock.release()

    def pause(self) -> bool:
        with self._state_lock:
            if not self._active_task_id:
                return False
            self._paused = True
        return self._send_control("pause", task_id=self._active_task_id)

    def resume(self) -> bool:
        with self._state_lock:
            if not self._active_task_id:
                return False
            self._paused = False
        return self._send_control("resume", task_id=self._active_task_id)

    def cancel(self, *, reason: str = "user_requested") -> bool:
        with self._state_lock:
            task_id = self._active_task_id
        if not task_id:
            return False
        return self._send_control("cancel", task_id=task_id, reason=str(reason or "user_requested"))

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                self._send({"command": "shutdown", "request_id": uuid.uuid4().hex})
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            self._hard_stop()


__all__ = [
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "UFO_COMMIT",
    "UFO_TAG",
    "UFO_VERSION",
    "UfoDriverConfig",
    "UfoWindowsDriver",
]
