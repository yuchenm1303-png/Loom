from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

MAX_ARTIFACT_BYTES = 48 * 1024 * 1024
_RUNTIME_ROOT = Path(os.environ.get("LOOM_WEB_RUNTIME_ROOT", "/var/lib/loom-web")).resolve()
_APP_SERVER_SCRIPT = Path(os.environ.get("LOOM_WEB_APP_SERVER_SCRIPT", "/opt/loom/loom_app_server.py")).resolve()
_RUNTIME_PYTHONPATH = os.environ.get("LOOM_WEB_PYTHONPATH", "/opt/loom")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

NotificationSink = Callable[[int, dict[str, Any]], Awaitable[None]]


class CloudRuntimeError(RuntimeError):
    pass


def _env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _secret_from_file() -> str:
    path = _env("LOOM_WEB_API_KEY_FILE")
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CloudRuntimeError(f"Could not read LOOM_WEB_API_KEY_FILE: {exc}") from exc


@dataclass(frozen=True)
class CloudModelConfig:
    provider: str
    base_url: str
    model: str
    selection: str
    api_key: str
    vision: bool

    @classmethod
    def from_environment(cls) -> "CloudModelConfig":
        provider = _env("LOOM_WEB_PROVIDER", "LOOM_PROVIDER") or "openai-compatible"
        base_url = _env("LOOM_WEB_BASE_URL", "LOOM_BASE_URL")
        model = _env("LOOM_WEB_MODEL", "LOOM_MODEL", "AGENT_MODEL")
        selection = _env("LOOM_WEB_SELECTION") or "cloud:default"
        api_key = _secret_from_file() or _env(
            "LOOM_WEB_API_KEY",
            "LOOM_API_KEY",
            "AI_API_KEY",
            "OPENAI_API_KEY",
            "DASHSCOPE_API_KEY",
        )
        vision = _env("LOOM_WEB_VISION").casefold() not in {"0", "false", "no", "off"}
        return cls(
            provider=provider,
            base_url=base_url,
            model=model,
            selection=selection,
            api_key=api_key,
            vision=vision,
        )

    @property
    def configured(self) -> bool:
        if not self.model or not self.api_key:
            return False
        if self.provider == "openai-compatible" and not self.base_url:
            return False
        return True

    def profile(self) -> dict[str, Any]:
        name = self.model or "Configure Loom Web model"
        status = "" if self.configured else "Loom Web needs a server-side model endpoint and API key."
        return {
            "selection": self.selection,
            "id": self.selection,
            "kind": "builtin",
            "name": name,
            "adapter": self.provider,
            "provider": self.provider,
            "baseUrl": self.base_url,
            "model": self.model or "unconfigured",
            "groupId": "loom-web",
            "groupName": "Loom Web",
            "groupOrder": 0,
            "family": "cloud",
            "protocol": "openai-compatible" if self.provider != "openai" else "openai",
            "configured": self.configured,
            "available": self.configured,
            "catalogSource": "runtime",
            "statusMessage": status or None,
            "vision": self.vision,
            "reasoning": None,
        }

    def snapshot(self) -> dict[str, Any]:
        profile = self.profile()
        current = dict(profile)
        return {
            "primary": profile,
            "profiles": [profile],
            "activeModelId": self.selection,
            "current": current,
            "recentModels": [self.model] if self.model else [],
            "catalogRefreshedAt": 0,
            "catalogTtlMs": 300_000,
        }


class CloudRuntimeProcess:
    def __init__(
        self,
        *,
        user_id: int,
        root: Path,
        model: CloudModelConfig,
        notification_sink: NotificationSink,
    ) -> None:
        self.user_id = user_id
        self.root = root
        self.model = model
        self.notification_sink = notification_sink
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.next_id = 1
        self.initialized = False
        self.initialize_result: Any = None
        self.start_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def home(self) -> Path:
        return self.root / "runtime"

    async def connect(self) -> Any:
        if self.process is not None and self.initialized:
            return self.initialize_result
        await self._ensure_started()
        result = await self._call_raw(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "loom-web", "version": "1.0.0"},
            },
        )
        await self._notify("initialized", {})
        self.initialize_result = result
        self.initialized = True
        return result

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not method:
            raise CloudRuntimeError("App Server method is required.")
        if not self.initialized:
            await self.connect()
        return await self._call_raw(method, params or {})

    async def _ensure_started(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        async with self.start_lock:
            if self.process is not None and self.process.returncode is None:
                return
            if not self.model.configured:
                raise CloudRuntimeError(
                    "Loom Web Agent is not configured. Set LOOM_WEB_MODEL and LOOM_WEB_API_KEY "
                    "(plus LOOM_WEB_BASE_URL for OpenAI-compatible providers) on the Loom Web runtime."
                )
            self.workspace.mkdir(parents=True, exist_ok=True)
            self.home.mkdir(parents=True, exist_ok=True)
            if not _APP_SERVER_SCRIPT.is_file():
                raise CloudRuntimeError(f"Loom App Server is missing: {_APP_SERVER_SCRIPT}")

            args = [
                sys.executable,
                "-u",
                str(_APP_SERVER_SCRIPT),
                "--home",
                str(self.home),
                "--workspace",
                str(self.workspace),
                "--provider",
                self.model.provider,
                "--model",
                self.model.model,
                "--selection",
                self.model.selection,
                "--permission-mode",
                _env("LOOM_WEB_PERMISSION_MODE") or "workspace",
            ]
            if self.model.base_url:
                args.extend(["--base-url", self.model.base_url])
            args.append("--vision" if self.model.vision else "--no-vision")

            env = dict(os.environ)
            env.update(
                {
                    "PYTHONUTF8": "1",
                    "PYTHONPATH": os.pathsep.join(
                        part for part in (_RUNTIME_PYTHONPATH, env.get("PYTHONPATH", "")) if part
                    ),
                    "LOOM_HOME": str(self.home),
                    "LOOM_PROVIDER": self.model.provider,
                    "LOOM_MODEL": self.model.model,
                    "LOOM_API_KEY": self.model.api_key,
                }
            )
            if self.model.base_url:
                env["LOOM_BASE_URL"] = self.model.base_url

            self.process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self.workspace),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.initialized = False
            self.initialize_result = None
            self.reader_task = asyncio.create_task(self._read_stdout())
            self.stderr_task = asyncio.create_task(self._read_stderr())

    async def _call_raw(self, method: str, params: dict[str, Any]) -> Any:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CloudRuntimeError("Loom Web App Server is not running.")
        request_id = self.next_id
        self.next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self.pending[request_id] = future
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with self.write_lock:
                process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
                await process.stdin.drain()
        except Exception:
            self.pending.pop(request_id, None)
            raise
        return await future

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            return
        frame = {"jsonrpc": "2.0", "method": method, "params": params}
        async with self.write_lock:
            process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("method"):
                    await self.notification_sink(self.user_id, payload)
                    continue
                request_id = payload.get("id")
                if not isinstance(request_id, int):
                    continue
                future = self.pending.pop(request_id, None)
                if future is None or future.done():
                    continue
                error = payload.get("error")
                if isinstance(error, dict):
                    future.set_exception(CloudRuntimeError(str(error.get("message") or "Loom Web App Server error.")))
                else:
                    future.set_result(payload.get("result"))
        finally:
            code = await process.wait()
            self.process = None
            self.initialized = False
            self.initialize_result = None
            error = CloudRuntimeError(f"Loom Web App Server exited ({code}).")
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()

    async def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        while True:
            raw = await process.stderr.readline()
            if not raw:
                return
            # Never mirror model credentials or request headers. The App Server's
            # stderr is diagnostic only; keep a short redacted breadcrumb.
            text = raw.decode("utf-8", errors="replace").strip()
            if self.model.api_key:
                text = text.replace(self.model.api_key, "[redacted]")
            text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[redacted]", text)
            if text:
                print(f"[loom-web-runtime user={self.user_id}] {text[:1200]}", file=sys.stderr, flush=True)

    async def close(self) -> None:
        process = self.process
        self.process = None
        self.initialized = False
        self.initialize_result = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


class CloudRuntimePool:
    def __init__(self, notification_sink: NotificationSink) -> None:
        self.notification_sink = notification_sink
        self.model = CloudModelConfig.from_environment()
        self.processes: dict[int, CloudRuntimeProcess] = {}
        self.lock = asyncio.Lock()

    def user_root(self, user_id: int) -> Path:
        if user_id <= 0:
            raise CloudRuntimeError("Invalid Loom account.")
        return _RUNTIME_ROOT / "users" / str(user_id)

    async def runtime(self, user_id: int) -> CloudRuntimeProcess:
        async with self.lock:
            runtime = self.processes.get(user_id)
            if runtime is None:
                runtime = CloudRuntimeProcess(
                    user_id=user_id,
                    root=self.user_root(user_id),
                    model=self.model,
                    notification_sink=self.notification_sink,
                )
                self.processes[user_id] = runtime
            return runtime

    def model_snapshot(self) -> dict[str, Any]:
        return self.model.snapshot()

    async def invoke(self, user_id: int, operation: str, args: list[Any]) -> Any:
        if operation == "listModels":
            return self.model_snapshot()
        if operation == "connect":
            runtime = await self.runtime(user_id)
            return await runtime.connect()
        if operation == "call":
            if len(args) < 1:
                raise CloudRuntimeError("App Server method is required.")
            method = str(args[0] or "")
            params = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            runtime = await self.runtime(user_id)
            return await runtime.call(method, params)
        if operation == "setReasoning":
            runtime = await self.runtime(user_id)
            if len(args) == 2:
                result = await runtime.call(
                    "runtime/set_reasoning",
                    {"kind": str(args[0] or ""), "value": str(args[1] or "")},
                )
                return {"runtime": result, "models": self.model_snapshot()}
            if len(args) >= 5:
                result = await runtime.call(
                    "thread/set_reasoning",
                    {
                        "threadId": str(args[0] or ""),
                        "kind": str(args[3] or ""),
                        "value": str(args[4] or "")},
                )
                payload = result if isinstance(result, dict) else {}
                return {
                    "runtime": payload.get("runtime", {}),
                    "thread": payload.get("thread"),
                    "models": self.model_snapshot(),
                }
            raise CloudRuntimeError("Invalid reasoning request.")
        if operation in {"switchModelProfile", "switchCurrentModel"}:
            requested = ""
            if operation == "switchModelProfile":
                requested = str(args[-1] or "") if args else ""
            elif len(args) >= 2:
                requested = str(args[-2] or "")
            if requested and requested != self.model.selection:
                raise CloudRuntimeError("Loom Web currently exposes the server-managed cloud model only.")
            runtime = await self.runtime(user_id)
            initialization = await runtime.connect()
            return {"initialization": initialization, "models": self.model_snapshot(), "hotSwitch": True}
        if operation == "testModel":
            profile = self.model.profile()
            return {
                "ok": self.model.configured,
                "selection": self.model.selection,
                "status": 200 if self.model.configured else 503,
                "latencyMs": 0,
                "endpoint": self.model.base_url,
                "model": self.model.model,
                "modelListed": self.model.configured,
                "discoveredModels": 1 if self.model.configured else 0,
                "capabilities": {
                    "chat": self.model.configured,
                    "streaming": self.model.configured,
                    "vision": bool(profile.get("vision")),
                    "reasoning": False,
                },
            }
        if operation in {"setModelProviderKey", "addModel", "updateModel", "deleteModel"}:
            raise CloudRuntimeError(
                "Loom Web model credentials are managed by the cloud runtime. Desktop model settings remain independent."
            )
        if operation == "stageTempFile":
            return self.stage_file(user_id, args)
        if operation in {"readLocalArtifact", "readLocalImage", "readLocalMedia"}:
            return self.read_artifact(user_id, args)
        if operation in {"exportComputerLogs", "exportBrowserLogs"}:
            return ""
        raise CloudRuntimeError(f"Unsupported Loom Web operation: {operation}")

    def stage_file(self, user_id: int, args: list[Any]) -> str:
        name = str(args[0] or "attachment") if args else "attachment"
        encoded = str(args[1] or "") if len(args) > 1 else ""
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise CloudRuntimeError("Attachment payload is not valid base64.") from exc
        if len(data) > MAX_ARTIFACT_BYTES:
            raise CloudRuntimeError("Attachment is too large for Loom Web (48 MB max).")
        safe_name = _SAFE_NAME_RE.sub("_", Path(name).name).strip("._") or "attachment"
        target_dir = self.user_root(user_id) / "workspace" / ".loom" / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex[:12]}-{safe_name}"
        target.write_bytes(data)
        return str(target)

    def read_artifact(self, user_id: int, args: list[Any]) -> dict[str, str]:
        if not args:
            raise CloudRuntimeError("Artifact path is required.")
        workspace = (self.user_root(user_id) / "workspace").resolve()
        raw = Path(str(args[0] or ""))
        candidate = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
        if candidate != workspace and workspace not in candidate.parents:
            raise CloudRuntimeError("Artifact path is outside the Loom Web workspace.")
        if not candidate.is_file():
            raise CloudRuntimeError("Artifact does not exist in the Loom Web workspace.")
        size = candidate.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise CloudRuntimeError("Artifact is too large to preview in Loom Web.")
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return {
            "base64": base64.b64encode(candidate.read_bytes()).decode("ascii"),
            "mimeType": mime,
        }

    async def close(self) -> None:
        async with self.lock:
            processes = list(self.processes.values())
            self.processes.clear()
        await asyncio.gather(*(runtime.close() for runtime in processes), return_exceptions=True)