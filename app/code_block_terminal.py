from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .agent_runtime.contracts import PermissionMode
from .agent_runtime.permissions import permission_snapshot


_EXECUTABLE_LANGUAGES = {
    "bash",
    "bat",
    "batch",
    "cmd",
    "console",
    "powershell",
    "ps1",
    "pwsh",
    "sh",
    "shell",
    "terminal",
    "zsh",
}


def _workspace(value: object) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("workspace is required")
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("workspace must be an existing directory")
    return path


def _session_id(workspace: Path) -> str:
    digest = hashlib.sha256(str(workspace).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"code-block-terminal-{digest}"


def _shell_argv(language: str, command: str) -> tuple[str, ...]:
    lang = str(language or "").strip().lower()
    if lang not in _EXECUTABLE_LANGUAGES:
        raise ValueError(f"Code block language is not executable: {language or 'code'}")
    if not command.strip():
        raise ValueError("command must not be empty")

    if os.name == "nt":
        if lang in {"cmd", "bat", "batch"}:
            executable = os.environ.get("COMSPEC") or shutil.which("cmd") or "cmd.exe"
            return executable, "/d", "/s", "/c", command
        if lang in {"bash", "sh", "zsh"}:
            executable = shutil.which(lang)
            if executable:
                return executable, "-lc", command
        executable = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        return executable, "-NoLogo", "-NoProfile", "-Command", command

    if lang in {"powershell", "pwsh", "ps1"}:
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise RuntimeError("PowerShell is not installed on this machine")
        return executable, "-NoLogo", "-NoProfile", "-Command", command
    if lang in {"cmd", "bat", "batch"}:
        raise RuntimeError("cmd code blocks can only run on Windows")
    if lang in {"bash", "zsh"}:
        executable = shutil.which(lang)
        if not executable:
            raise RuntimeError(f"{lang} is not installed on this machine")
        return executable, "-lc", command
    if lang == "sh":
        return shutil.which("sh") or "/bin/sh", "-c", command

    configured = os.environ.get("SHELL", "").strip()
    executable = configured if configured and Path(configured).exists() else (shutil.which("sh") or "/bin/sh")
    login_shell = Path(executable).name in {"bash", "zsh", "fish"}
    return executable, "-lc" if login_shell else "-c", command


def _snapshot(managed, *, drain_delta: bool = True) -> dict[str, Any]:
    snapshot = managed.snapshot(drain_delta=drain_delta).to_dict()
    return {
        "processId": snapshot["process_id"],
        "state": snapshot["state"],
        "running": snapshot["running"],
        "returnCode": snapshot["returncode"],
        "stdout": snapshot["stdout"],
        "stderr": snapshot["stderr"],
        "stdoutDelta": snapshot["stdout_delta"],
        "stderrDelta": snapshot["stderr_delta"],
        "timedOut": snapshot["timed_out"],
        "outputTruncated": snapshot["output_truncated"],
        "failure": snapshot["failure"],
        "backend": snapshot["backend"],
        "pty": snapshot["pty"],
    }


def _run(service, params: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(params.get("workspace"))
    command = str(params.get("command") or "")
    language = str(params.get("language") or "shell")
    argv = _shell_argv(language, command)
    permissions = permission_snapshot(PermissionMode.WORKSPACE)
    managed = service.runtime.process_store.start(
        session_id=_session_id(workspace),
        argv=argv,
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE.value,
        permissions=permissions,
        timeout_seconds=3600,
        pty=True,
        rows=24,
        cols=120,
    )
    return _snapshot(managed)


def _process(service, params: dict[str, Any]):
    workspace = _workspace(params.get("workspace"))
    process_id = str(params.get("processId") or "").strip()
    if not process_id:
        raise ValueError("processId is required")
    return service.runtime.process_store.get(
        process_id,
        session_id=_session_id(workspace),
    )


def _dispatch(service, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "terminal/run":
        return _run(service, params)

    managed = _process(service, params)
    if method == "terminal/read":
        return _snapshot(managed)
    if method == "terminal/write":
        text = str(params.get("text") or "")
        if not text:
            raise ValueError("text must not be empty")
        managed.write_stdin(text)
        return _snapshot(managed)
    if method == "terminal/interrupt":
        managed.interrupt()
        return _snapshot(managed)
    if method == "terminal/terminate":
        managed.terminate_tree()
        return _snapshot(managed)
    raise RuntimeError(f"Unknown code-block terminal method: {method}")


def _install_on_app_server(module) -> None:
    controller_cls = getattr(module, "LoomRpcController", None)
    if controller_cls is None or getattr(controller_cls, "_loom_code_block_terminal", False):
        return

    original_dispatch = controller_cls._dispatch
    original_initialize = controller_cls._initialize

    def patched_dispatch(self, method: str, params: dict[str, Any]):
        if method.startswith("terminal/"):
            return _dispatch(self.service, method, params)
        return original_dispatch(self, method, params)

    def patched_initialize(self, params: dict[str, Any]):
        result = original_initialize(self, params)
        capabilities = result.get("capabilities")
        if isinstance(capabilities, dict):
            capabilities["codeBlockTerminal"] = {
                "run": True,
                "interactive": True,
                "interrupt": True,
                "sandbox": "workspace-write",
            }
        return result

    controller_cls._dispatch = patched_dispatch
    controller_cls._initialize = patched_initialize
    controller_cls._loom_code_block_terminal = True


def install() -> None:
    _install_on_app_server(importlib.import_module("app.app_server"))
    for name, module in tuple(sys.modules.items()):
        if name == "app_server" or name.endswith(".app_server"):
            _install_on_app_server(module)
