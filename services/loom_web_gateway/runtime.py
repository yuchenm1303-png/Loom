from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.ai.model_context import model_context_limits_from_provider_listing, model_context_limits_to_camel
from app.ai.reasoning_catalog import reasoning_capability

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
        base_url = _env("LOOM_WEB_BASE_URL", "LOOM_BASE_URL") or "https://muxway.dev/v1"
        model = _env("LOOM_WEB_MODEL", "LOOM_MODEL", "AGENT_MODEL") or "MiniMax-M3"
        explicit_selection = _env("LOOM_WEB_SELECTION")
        selection = explicit_selection if explicit_selection.startswith("managed:") else _managed_selection(model)
        api_key = _secret_from_file() or _env(
            "LOOM_WEB_API_KEY",
            "LOOM_RELAY_API_KEY",
            "SMIREL_RELAY_API_KEY",
            "LOOM_API_KEY",
            "AI_API_KEY",
            "OPENAI_API_KEY",
            "DASHSCOPE_API_KEY",
        )
        vision = _env("LOOM_WEB_VISION").casefold() not in {"0", "false", "no", "off"}
        return cls(
            provider=provider,
            base_url=base_url.rstrip("/"),
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

    def for_model(self, model: str, *, selection: str = "", vision: bool | None = None) -> "CloudModelConfig":
        value = str(model or "").strip()
        if not value:
            raise CloudRuntimeError("Model id is required.")
        return replace(
            self,
            model=value,
            selection=str(selection or _managed_selection(value)).strip(),
            vision=self.vision if vision is None else bool(vision),
        )


def _managed_selection(model: str) -> str:
    return "managed:" + urllib.parse.quote(str(model or "").strip(), safe="")


def _managed_group(model: str) -> tuple[str, str, int]:
    folded = str(model or "").strip().casefold().replace("_", "-")
    while "--" in folded:
        folded = folded.replace("--", "-")
    if folded.startswith("minimax-"):
        return "managed-relay:minimax", "MiniMax · Muxway", 15
    if folded.startswith(("gpt-", "chatgpt-", "codex-", "o1", "o3", "o4")):
        return "managed-relay:openai", "OpenAI", 40
    return "managed-relay", "Muxway Relay", 45


def _display_name(model: str) -> str:
    value = str(model or "").strip()
    known = {
        "minimax-m3": "MiniMax M3",
        "minimax-m2.7": "MiniMax M2.7",
        "minimax-m2.5": "MiniMax M2.5",
        "cqu-default": "CQU-弘深深",
    }
    return known.get(value.casefold(), value)


def _profile_from_listing(config: CloudModelConfig, entry: dict[str, Any], *, source: str) -> dict[str, Any]:
    model = str(entry.get("id") or "").strip()
    if not model:
        raise CloudRuntimeError("Muxway returned a model without an id.")
    group_id, group_name, group_order = _managed_group(model)
    limits = model_context_limits_from_provider_listing(entry)
    context_limits = {
        key: value for key, value in model_context_limits_to_camel(limits).items() if value is not None
    }
    capability = reasoning_capability(model=model, adapter="openai-compatible", base_url=config.base_url)
    reasoning = None
    if capability is not None:
        reasoning = {**capability, "value": str(capability.get("defaultValue") or "")}
    profile: dict[str, Any] = {
        "selection": _managed_selection(model),
        "id": "managed-" + hashlib.sha256(model.casefold().encode("utf-8")).hexdigest()[:12],
        "kind": "builtin",
        "name": _display_name(model),
        "adapter": "openai-compatible",
        "provider": "openai-compatible",
        "baseUrl": config.base_url,
        "model": model,
        "groupId": group_id,
        "groupName": group_name,
        "groupOrder": group_order,
        "configured": config.configured,
        "available": config.configured,
        "catalogSource": source,
        "vision": config.vision,
        "reasoning": reasoning,
    }
    if context_limits:
        profile["contextLimits"] = context_limits
    return profile


def _fallback_profile(config: CloudModelConfig, *, status: str = "") -> dict[str, Any]:
    entry = {"id": config.model or "MiniMax-M3"}
    profile = _profile_from_listing(config, entry, source="fallback")
    if not config.api_key:
        profile["configured"] = False
        profile["available"] = False
        profile["statusMessage"] = "Loom Web Relay credential is not configured."
    elif status:
        profile["statusMessage"] = status
    return profile


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
                    "LOOM_RELAY_API_KEY": self.model.api_key,
                    "LOOM_RELAY_BASE_URL": self.model.base_url,
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
    CATALOG_TTL_SECONDS = 300.0

    def __init__(self, notification_sink: NotificationSink) -> None:
        self.notification_sink = notification_sink
        self.model = CloudModelConfig.from_environment()
        self.processes: dict[int, CloudRuntimeProcess] = {}
        self.lock = asyncio.Lock()
        self.catalog_lock = asyncio.Lock()
        self.catalog_cache: list[dict[str, Any]] = []
        self.catalog_cache_at = 0.0

    def user_root(self, user_id: int) -> Path:
        if user_id <= 0:
            raise CloudRuntimeError("Invalid Loom account.")
        return _RUNTIME_ROOT / "users" / str(user_id)

    def _selection_path(self, user_id: int) -> Path:
        return self.user_root(user_id) / "selected-model.json"

    def _load_selected_model(self, user_id: int) -> str:
        path = self._selection_path(user_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("model") or "").strip()

    def _save_selected_model(self, user_id: int, profile: dict[str, Any]) -> None:
        path = self._selection_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {
                    "model": str(profile.get("model") or ""),
                    "selection": str(profile.get("selection") or ""),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp.replace(path)

    def _fetch_catalog_sync(self) -> list[dict[str, Any]]:
        if not self.model.api_key:
            return [_fallback_profile(self.model)]
        request = urllib.request.Request(
            self.model.base_url.rstrip("/") + "/models",
            headers={
                "Authorization": "Bearer " + self.model.api_key,
                "Accept": "application/json",
                "User-Agent": "Loom-Web/managed-relay",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return [_fallback_profile(self.model, status=f"Muxway model catalog returned HTTP {exc.code}.")]
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return [_fallback_profile(self.model, status="Muxway model catalog is temporarily unavailable.")]
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return [_fallback_profile(self.model, status="Muxway returned an invalid model catalog.")]
        profiles: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            model = str(item.get("id") or "").strip()
            folded = model.casefold()
            if not model or folded in seen:
                continue
            seen.add(folded)
            profiles.append(_profile_from_listing(self.model, item, source="provider"))
        return profiles or [_fallback_profile(self.model, status="Muxway model catalog is empty.")]

    async def catalog_profiles(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if (
            not force_refresh
            and self.catalog_cache
            and now - self.catalog_cache_at < self.CATALOG_TTL_SECONDS
        ):
            return [dict(profile) for profile in self.catalog_cache]
        async with self.catalog_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self.catalog_cache
                and now - self.catalog_cache_at < self.CATALOG_TTL_SECONDS
            ):
                return [dict(profile) for profile in self.catalog_cache]
            profiles = await asyncio.to_thread(self._fetch_catalog_sync)
            self.catalog_cache = [dict(profile) for profile in profiles]
            self.catalog_cache_at = time.monotonic()
            return profiles

    async def _profile_for(
        self,
        *,
        selection: str = "",
        model: str = "",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        profiles = await self.catalog_profiles(force_refresh)
        wanted_selection = str(selection or "").strip()
        wanted_model = str(model or "").strip().casefold()
        if wanted_selection:
            for profile in profiles:
                if str(profile.get("selection") or "") == wanted_selection:
                    return profile
        if wanted_model:
            for profile in profiles:
                if str(profile.get("model") or "").strip().casefold() == wanted_model:
                    return profile
        raise CloudRuntimeError("The requested model is not available in this Loom Web Relay entitlement.")

    def _config_for_profile(self, profile: dict[str, Any]) -> CloudModelConfig:
        return self.model.for_model(
            str(profile.get("model") or ""),
            selection=str(profile.get("selection") or ""),
            vision=bool(profile.get("vision", True)),
        )

    async def _current_profile(self, user_id: int, *, force_refresh: bool = False) -> dict[str, Any]:
        profiles = await self.catalog_profiles(force_refresh)
        runtime = self.processes.get(user_id)
        current_model = runtime.model.model if runtime is not None else self._load_selected_model(user_id)
        if not current_model:
            current_model = self.model.model
        for profile in profiles:
            if str(profile.get("model") or "").strip().casefold() == current_model.casefold():
                return profile
        default = self.model.model.casefold()
        for profile in profiles:
            if str(profile.get("model") or "").strip().casefold() == default:
                return profile
        return profiles[0]

    async def runtime(self, user_id: int) -> CloudRuntimeProcess:
        async with self.lock:
            runtime = self.processes.get(user_id)
            if runtime is not None:
                return runtime
        profile = await self._current_profile(user_id)
        config = self._config_for_profile(profile)
        async with self.lock:
            runtime = self.processes.get(user_id)
            if runtime is None:
                runtime = CloudRuntimeProcess(
                    user_id=user_id,
                    root=self.user_root(user_id),
                    model=config,
                    notification_sink=self.notification_sink,
                )
                self.processes[user_id] = runtime
            return runtime

    async def model_snapshot(
        self,
        user_id: int,
        *,
        force_refresh: bool = False,
        current_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profiles = await self.catalog_profiles(force_refresh)
        current = current_override or await self._current_profile(user_id, force_refresh=False)
        default_model = self.model.model.casefold()
        primary = next(
            (profile for profile in profiles if str(profile.get("model") or "").casefold() == default_model),
            profiles[0],
        )
        return {
            "primary": dict(primary),
            "profiles": [dict(profile) for profile in profiles],
            "activeModelId": str(current.get("id") or current.get("selection") or ""),
            "current": dict(current),
            "recentModels": [str(current.get("model") or "")],
            "catalogRefreshedAt": int(time.time() * 1000),
            "catalogTtlMs": int(self.CATALOG_TTL_SECONDS * 1000),
        }

    @staticmethod
    def _runtime_model_params(profile: dict[str, Any], api_key: str) -> dict[str, Any]:
        reasoning = profile.get("reasoning") if isinstance(profile.get("reasoning"), dict) else {}
        return {
            "selection": str(profile.get("selection") or ""),
            "provider": "openai-compatible",
            "baseUrl": str(profile.get("baseUrl") or ""),
            "model": str(profile.get("model") or ""),
            "apiKey": api_key,
            "vision": bool(profile.get("vision", True)),
            "contextLimits": profile.get("contextLimits") if isinstance(profile.get("contextLimits"), dict) else {},
            "reasoningKind": str(reasoning.get("kind") or ""),
            "reasoningValue": str(reasoning.get("value") or ""),
        }

    async def _switch_global(self, user_id: int, profile: dict[str, Any]) -> dict[str, Any]:
        runtime = await self.runtime(user_id)
        result = await runtime.call("runtime/set_model", self._runtime_model_params(profile, self.model.api_key))
        runtime.model = self._config_for_profile(profile)
        self._save_selected_model(user_id, profile)
        return {
            "initialization": {"runtime": result},
            "models": await self.model_snapshot(user_id),
            "hotSwitch": True,
        }

    async def _switch_thread(self, user_id: int, thread_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        runtime = await self.runtime(user_id)
        result = await runtime.call(
            "thread/set_model",
            {
                "threadId": str(thread_id or "").strip(),
                "selection": str(profile.get("selection") or ""),
                "model": str(profile.get("model") or ""),
            },
        )
        payload = result if isinstance(result, dict) else {}
        return {
            "initialization": {"runtime": payload.get("runtime", {})},
            "models": await self.model_snapshot(user_id, current_override=profile),
            "hotSwitch": True,
            "thread": payload.get("thread"),
        }

    async def invoke(self, user_id: int, operation: str, args: list[Any]) -> Any:
        if operation == "listModels":
            return await self.model_snapshot(user_id, force_refresh=bool(args[0]) if args else False)
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
                return {"runtime": result, "models": await self.model_snapshot(user_id)}
            if len(args) >= 5:
                result = await runtime.call(
                    "thread/set_reasoning",
                    {
                        "threadId": str(args[0] or ""),
                        "kind": str(args[3] or ""),
                        "value": str(args[4] or ""),
                    },
                )
                payload = result if isinstance(result, dict) else {}
                return {
                    "runtime": payload.get("runtime", {}),
                    "thread": payload.get("thread"),
                    "models": await self.model_snapshot(user_id),
                }
            raise CloudRuntimeError("Invalid reasoning request.")
        if operation == "switchModelProfile":
            if not args:
                raise CloudRuntimeError("Model profile is required.")
            if len(args) == 1:
                profile = await self._profile_for(selection=str(args[0] or ""))
                return await self._switch_global(user_id, profile)
            profile = await self._profile_for(selection=str(args[1] or ""))
            return await self._switch_thread(user_id, str(args[0] or ""), profile)
        if operation == "switchCurrentModel":
            if not args:
                raise CloudRuntimeError("Model id is required.")
            if len(args) == 1:
                profile = await self._profile_for(model=str(args[0] or ""))
                return await self._switch_global(user_id, profile)
            if len(args) >= 3:
                profile = await self._profile_for(
                    selection=str(args[1] or ""),
                    model=str(args[2] or ""),
                )
                return await self._switch_thread(user_id, str(args[0] or ""), profile)
            raise CloudRuntimeError("Invalid model switch request.")
        if operation == "testModel":
            selection = str(args[0] or "") if args else ""
            profile = await self._profile_for(selection=selection, force_refresh=True)
            capability = profile.get("reasoning") if isinstance(profile.get("reasoning"), dict) else None
            return {
                "ok": bool(profile.get("available")),
                "selection": str(profile.get("selection") or ""),
                "status": 200 if profile.get("available") else 503,
                "latencyMs": 0,
                "endpoint": str(profile.get("baseUrl") or ""),
                "model": str(profile.get("model") or ""),
                "modelListed": profile.get("catalogSource") == "provider",
                "discoveredModels": len(await self.catalog_profiles()),
                "capabilities": {
                    "chat": bool(profile.get("available")),
                    "streaming": bool(profile.get("available")),
                    "vision": bool(profile.get("vision")),
                    "reasoning": capability is not None,
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