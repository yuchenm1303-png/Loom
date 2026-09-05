from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .browser_runtime_v1 import BrowserRuntime
from .contracts import ToolEffect
from .memory_store import redact_secrets
from .tools import AgentTool, ToolContext, ToolExposure, ToolResult


_MCP_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_SUPPORTED_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


class MCPConfigurationError(ValueError):
    pass


class MCPUnavailableError(RuntimeError):
    pass


def mcp_sdk_available() -> bool:
    try:
        import mcp  # noqa: F401
    except Exception:
        return False
    return True


def _canonical_segment(value: str, *, fallback: str) -> str:
    segment = _MCP_SAFE_NAME.sub("_", str(value or "").strip()).strip(".-")
    if not segment:
        segment = fallback
    if not (segment[0].isalpha() or segment[0] == "_"):
        segment = "_" + segment
    return segment[:56]


def canonical_mcp_tool_name(server_name: str, tool_name: str) -> str:
    server = _canonical_segment(server_name, fallback="server")
    tool = _canonical_segment(tool_name, fallback="tool")
    value = f"mcp.{server}.{tool}"
    return value[:128]


def _schema_subset(schema: Any) -> dict[str, Any]:
    """Keep the JSON-Schema subset Loom validates locally.

    MCP 2026-07-28 tools may use full JSON Schema 2020-12. Loom's local
    validator intentionally remains small, so unsupported composition keywords
    are left for the MCP server to validate rather than causing false rejects.
    """

    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    result: dict[str, Any] = {}
    raw_type = schema.get("type")
    if isinstance(raw_type, str) and raw_type in _SUPPORTED_SCHEMA_TYPES:
        result["type"] = raw_type

    enum = schema.get("enum")
    if isinstance(enum, list):
        result["enum"] = list(enum)

    if result.get("type") == "object" or "properties" in schema:
        result["type"] = "object"
        properties = schema.get("properties")
        if isinstance(properties, dict):
            result["properties"] = {
                str(key): _schema_subset(value) if isinstance(value, dict) else {}
                for key, value in properties.items()
            }
        else:
            result["properties"] = {}
        required = schema.get("required")
        if isinstance(required, list):
            result["required"] = [str(item) for item in required if isinstance(item, str)]
        if schema.get("additionalProperties") is False:
            result["additionalProperties"] = False
    elif result.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            result["items"] = _schema_subset(items)

    if "type" not in result:
        # Unknown/composed leaf schemas are deliberately permissive locally;
        # the remote MCP server remains the authoritative validator.
        return {}
    return result


def _bounded_json(value: Any, *, max_chars: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        text = str(value)
    limit = max(256, int(max_chars))
    if len(text) <= limit:
        return text
    return text[: limit - 18] + "…[truncated]"


def _normalize_mcp_result(result: Any, *, max_chars: int) -> ToolResult:
    pieces: list[str] = []
    content = getattr(result, "content", None) or []
    for item in content:
        kind = str(getattr(item, "type", "") or "").casefold()
        if kind == "text":
            pieces.append(str(getattr(item, "text", "") or ""))
            continue
        if kind in {"image", "audio"}:
            mime = str(getattr(item, "mime_type", "") or getattr(item, "mimeType", "") or "unknown")
            pieces.append(f"[{kind} content omitted from durable tool result; mime={mime}]")
            continue
        if kind in {"resource", "resource_link"}:
            resource = getattr(item, "resource", None)
            if resource is not None:
                text = getattr(resource, "text", None)
                if text is not None:
                    pieces.append(str(text))
                    continue
                uri = getattr(resource, "uri", None)
                if uri is not None:
                    pieces.append(f"[resource: {uri}]")
                    continue
            uri = getattr(item, "uri", None)
            pieces.append(f"[resource: {uri}]" if uri is not None else "[resource content]")
            continue
        dumper = getattr(item, "model_dump", None)
        raw = dumper(mode="json", by_alias=True) if callable(dumper) else str(item)
        if isinstance(raw, dict):
            for key in ("data", "blob"):
                if key in raw:
                    raw[key] = "[binary omitted]"
        pieces.append(_bounded_json(raw, max_chars=max_chars // 2))

    structured = getattr(result, "structured_content", None)
    if structured not in (None, {}, []):
        structured_text = _bounded_json(structured, max_chars=max_chars // 2)
        if not pieces or structured_text not in pieces:
            pieces.append(structured_text)

    text = "\n".join(piece for piece in pieces if piece)
    if not text:
        text = "MCP tool completed without textual content."
    if len(text) > max_chars:
        text = text[: max_chars - 18] + "…[truncated]"
    is_error = bool(getattr(result, "is_error", False))
    return ToolResult(
        ok=not is_error,
        content=redact_secrets(text),
        data={"mcp": True, "is_error": is_error},
    )


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    name: str
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str = ""
    url: str = ""
    env_from: tuple[tuple[str, str], ...] = ()
    bearer_token_env: str = ""
    enabled: bool = True
    required: bool = False
    timeout_seconds: float = 30.0
    default_effect: ToolEffect = ToolEffect.SENSITIVE
    tool_effects: tuple[tuple[str, ToolEffect], ...] = ()
    exposure: ToolExposure = ToolExposure.DIRECT

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        transport = str(self.transport or "").strip().casefold()
        if not name:
            raise MCPConfigurationError("MCP server name must not be empty")
        if transport not in {"stdio", "http"}:
            raise MCPConfigurationError(f"MCP server {name!r} transport must be stdio or http")
        if transport == "stdio" and not str(self.command or "").strip():
            raise MCPConfigurationError(f"MCP stdio server {name!r} requires command")
        if transport == "http":
            url = str(self.url or "").strip()
            if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
                raise MCPConfigurationError(
                    f"MCP HTTP server {name!r} must use https, localhost, or 127.0.0.1"
                )
        timeout = float(self.timeout_seconds)
        if timeout <= 0:
            raise MCPConfigurationError("MCP timeout_seconds must be positive")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "command", str(self.command or "").strip())
        object.__setattr__(self, "args", tuple(str(item) for item in self.args))
        object.__setattr__(self, "cwd", str(self.cwd or "").strip())
        object.__setattr__(self, "url", str(self.url or "").strip())
        object.__setattr__(
            self,
            "env_from",
            tuple((str(child).strip(), str(source).strip()) for child, source in self.env_from),
        )
        object.__setattr__(self, "bearer_token_env", str(self.bearer_token_env or "").strip())
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "default_effect", ToolEffect(self.default_effect))
        object.__setattr__(
            self,
            "tool_effects",
            tuple((str(tool).strip(), ToolEffect(effect)) for tool, effect in self.tool_effects),
        )
        object.__setattr__(self, "exposure", ToolExposure(self.exposure))

    def effect_for(self, remote_tool_name: str) -> ToolEffect:
        wanted = str(remote_tool_name or "").strip()
        for name, effect in self.tool_effects:
            if name == wanted:
                return effect
        return self.default_effect

    def resolved_env(self) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for child_name, source_name in self.env_from:
            if not child_name or not source_name:
                raise MCPConfigurationError(f"MCP server {self.name!r} has invalid env_from mapping")
            value = os.environ.get(source_name)
            if value is None:
                raise MCPConfigurationError(
                    f"MCP server {self.name!r} requires environment variable {source_name!r}"
                )
            resolved[child_name] = value
        return resolved


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    canonical_name: str
    server_name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]
    effect: ToolEffect
    exposure: ToolExposure


@dataclass(slots=True)
class _ConnectedServer:
    config: MCPServerConfig
    client: Any
    http_client: Any = None
    descriptors: tuple[MCPToolDescriptor, ...] = ()
    instructions: str = ""
    protocol_version: str = ""
    server_info: str = ""


class _AsyncLoopRunner:
    def __init__(self, name: str = "loom-mcp") -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._closed = False
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    def run(
        self,
        coroutine: Any,
        *,
        timeout: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("MCP event-loop runner is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise TimeoutError("MCP operation timed out")
            try:
                return future.result(timeout=min(0.1, remaining))
            except concurrent.futures.TimeoutError:
                if cancel_check is not None and cancel_check():
                    future.cancel()
                    raise RuntimeError("agent turn cancellation requested")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3.0)


class MCPClientManager:
    """Owns configured MCP clients and adapts their tools into Loom AgentTool objects."""

    def __init__(
        self,
        configs: Sequence[MCPServerConfig] = (),
        *,
        max_result_chars: int = 20_000,
        target_factory: Callable[[MCPServerConfig], Any] | None = None,
    ) -> None:
        self.configs = tuple(config for config in configs if config.enabled)
        self.max_result_chars = max(1000, int(max_result_chars))
        self._target_factory = target_factory
        self._runner = _AsyncLoopRunner() if self.configs else None
        self._servers: dict[str, _ConnectedServer] = {}
        self._errors: dict[str, str] = {}
        self._closed = False

    def connect(self) -> None:
        if not self.configs:
            return
        if not mcp_sdk_available():
            message = "MCP Python SDK is not installed; install loom-agent[mcp]."
            for config in self.configs:
                self._errors[config.name] = message
                if config.required:
                    raise MCPUnavailableError(message)
            return

        for config in self.configs:
            if config.name in self._servers:
                continue
            try:
                connected = self._runner.run(
                    self._open_server(config),
                    timeout=config.timeout_seconds,
                )
            except Exception as exc:
                safe = redact_secrets(f"{type(exc).__name__}: {exc}")
                self._errors[config.name] = safe
                if config.required:
                    raise MCPUnavailableError(f"required MCP server {config.name!r} failed: {safe}") from exc
                continue
            self._servers[config.name] = connected
            self._errors.pop(config.name, None)

    async def _open_server(self, config: MCPServerConfig) -> _ConnectedServer:
        from mcp import Client, StdioServerParameters

        http_client = None
        if self._target_factory is not None:
            target = self._target_factory(config)
        elif config.transport == "stdio":
            target = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=config.resolved_env() or None,
                cwd=config.cwd or None,
            )
        elif config.bearer_token_env:
            token = os.environ.get(config.bearer_token_env)
            if not token:
                raise MCPConfigurationError(
                    f"MCP server {config.name!r} requires environment variable {config.bearer_token_env!r}"
                )
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
                timeout=httpx2.Timeout(config.timeout_seconds),
            )
            await http_client.__aenter__()
            target = streamable_http_client(config.url, http_client=http_client)
        else:
            target = config.url

        client = Client(target)
        try:
            await client.__aenter__()
            listed = await client.list_tools()
        except BaseException:
            try:
                await client.__aexit__(None, None, None)
            except BaseException:
                pass
            if http_client is not None:
                try:
                    await http_client.__aexit__(None, None, None)
                except BaseException:
                    pass
            raise

        instructions = redact_secrets(str(getattr(client, "instructions", "") or ""))[:2000]
        descriptors: list[MCPToolDescriptor] = []
        seen: set[str] = set()
        for remote in getattr(listed, "tools", ()) or ():
            remote_name = str(getattr(remote, "name", "") or "").strip()
            if not remote_name:
                continue
            canonical = canonical_mcp_tool_name(config.name, remote_name)
            if canonical in seen:
                raise MCPConfigurationError(
                    f"MCP tool name collision on server {config.name!r}: {remote_name!r} -> {canonical!r}"
                )
            seen.add(canonical)
            description = str(getattr(remote, "description", "") or f"MCP tool {remote_name}").strip()
            if instructions:
                description = f"{description}\nServer guidance: {instructions[:512]}"
            schema = _schema_subset(getattr(remote, "input_schema", None))
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            descriptors.append(
                MCPToolDescriptor(
                    canonical_name=canonical,
                    server_name=config.name,
                    remote_name=remote_name,
                    description=f"[MCP:{config.name}] {description}"[:4000],
                    input_schema=schema,
                    effect=config.effect_for(remote_name),
                    exposure=config.exposure,
                )
            )

        info = getattr(client, "server_info", None)
        info_name = str(getattr(info, "name", "") or "") if info is not None else ""
        info_version = str(getattr(info, "version", "") or "") if info is not None else ""
        return _ConnectedServer(
            config=config,
            client=client,
            http_client=http_client,
            descriptors=tuple(descriptors),
            instructions=instructions,
            protocol_version=str(getattr(client, "protocol_version", "") or ""),
            server_info="@".join(part for part in (info_name, info_version) if part),
        )

    def agent_tools(self) -> tuple[AgentTool, ...]:
        tools: list[AgentTool] = []
        canonical_seen: set[str] = set()
        for server_name in sorted(self._servers):
            connected = self._servers[server_name]
            for descriptor in connected.descriptors:
                if descriptor.canonical_name in canonical_seen:
                    raise MCPConfigurationError(f"duplicate canonical MCP tool: {descriptor.canonical_name}")
                canonical_seen.add(descriptor.canonical_name)

                def handler(
                    context: ToolContext,
                    arguments: dict[str, Any],
                    *,
                    _server=descriptor.server_name,
                    _tool=descriptor.remote_name,
                ) -> ToolResult:
                    context.raise_if_cancelled()
                    return self.call_tool(
                        _server,
                        _tool,
                        arguments,
                        cancel_check=lambda: context.cancelled,
                    )

                tools.append(
                    AgentTool(
                        name=descriptor.canonical_name,
                        description=descriptor.description,
                        input_schema=descriptor.input_schema,
                        handler=handler,
                        effect=descriptor.effect,
                        exposure=descriptor.exposure,
                    )
                )
        return tuple(tools)

    def call_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        arguments: Mapping[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ToolResult:
        connected = self._servers.get(str(server_name or "").strip())
        if connected is None:
            raise MCPUnavailableError(f"MCP server is not connected: {server_name}")
        if self._runner is None:
            raise MCPUnavailableError("MCP runtime is not active")
        result = self._runner.run(
            connected.client.call_tool(str(remote_tool_name), dict(arguments)),
            timeout=connected.config.timeout_seconds,
            cancel_check=cancel_check,
        )
        normalized = _normalize_mcp_result(result, max_chars=self.max_result_chars)
        data = dict(normalized.data)
        data.update({"server": connected.config.name, "tool": str(remote_tool_name)})
        return ToolResult(ok=normalized.ok, content=normalized.content, data=data)

    def status(self) -> dict[str, object]:
        servers: list[dict[str, object]] = []
        for config in self.configs:
            connected = self._servers.get(config.name)
            servers.append(
                {
                    "name": config.name,
                    "transport": config.transport,
                    "connected": connected is not None,
                    "protocol_version": connected.protocol_version if connected else "",
                    "server_info": connected.server_info if connected else "",
                    "tool_count": len(connected.descriptors) if connected else 0,
                    "error": self._errors.get(config.name, ""),
                }
            )
        return {
            "enabled": bool(self.configs),
            "sdk_available": mcp_sdk_available(),
            "connected_servers": len(self._servers),
            "tool_count": sum(len(item.descriptors) for item in self._servers.values()),
            "servers": servers,
        }

    async def _close_async(self) -> None:
        for name in reversed(tuple(self._servers)):
            connected = self._servers.pop(name)
            try:
                await connected.client.__aexit__(None, None, None)
            finally:
                if connected.http_client is not None:
                    await connected.http_client.__aexit__(None, None, None)

    def close(self) -> None:
        if self._closed:
            return
        if self._runner is not None:
            try:
                self._runner.run(self._close_async(), timeout=10.0)
            except Exception:
                pass
            self._runner.close()
        self._closed = True


class MCPRuntime(BrowserRuntime):
    """Top-level Runtime v2 layer that adds operator-configured MCP tools."""

    def __init__(
        self,
        *args: Any,
        mcp_servers: Sequence[MCPServerConfig] = (),
        auto_connect_mcp: bool = True,
        mcp_target_factory: Callable[[MCPServerConfig], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.mcp_clients = MCPClientManager(
            mcp_servers,
            max_result_chars=self.limits.max_tool_result_chars,
            target_factory=mcp_target_factory,
        )
        if auto_connect_mcp:
            self.mcp_clients.connect()
        for tool in self.mcp_clients.agent_tools():
            if self.tools.get(tool.name) is not None:
                raise MCPConfigurationError(f"MCP tool conflicts with existing Loom tool: {tool.name}")
            self.tools.register(tool)

    def mcp_status(self) -> dict[str, object]:
        return self.mcp_clients.status()

    def close(self) -> None:
        self.mcp_clients.close()
        super().close()


def load_mcp_server_configs(path: str | Path) -> tuple[MCPServerConfig, ...]:
    source = Path(path).expanduser()
    if not source.is_file():
        return ()
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    raw_servers = data.get("mcp_servers") or {}
    if not isinstance(raw_servers, dict):
        raise MCPConfigurationError("[mcp_servers] must be a TOML table")

    configs: list[MCPServerConfig] = []
    for name, raw in raw_servers.items():
        if not isinstance(raw, dict):
            raise MCPConfigurationError(f"MCP server {name!r} config must be a table")
        if "env" in raw or "bearer_token" in raw or "headers" in raw:
            raise MCPConfigurationError(
                f"MCP server {name!r} must not store credential values in config; use env_from or bearer_token_env"
            )
        transport = str(raw.get("transport") or ("stdio" if raw.get("command") else "http"))
        raw_env_from = raw.get("env_from") or {}
        if not isinstance(raw_env_from, dict):
            raise MCPConfigurationError(f"MCP server {name!r} env_from must be a table")
        raw_effects = raw.get("tool_effects") or {}
        if not isinstance(raw_effects, dict):
            raise MCPConfigurationError(f"MCP server {name!r} tool_effects must be a table")
        args = raw.get("args") or []
        if not isinstance(args, list):
            raise MCPConfigurationError(f"MCP server {name!r} args must be an array")
        configs.append(
            MCPServerConfig(
                name=str(name),
                transport=transport,
                command=str(raw.get("command") or ""),
                args=tuple(str(item) for item in args),
                cwd=str(raw.get("cwd") or ""),
                url=str(raw.get("url") or ""),
                env_from=tuple((str(child), str(source_env)) for child, source_env in raw_env_from.items()),
                bearer_token_env=str(raw.get("bearer_token_env") or ""),
                enabled=bool(raw.get("enabled", True)),
                required=bool(raw.get("required", False)),
                timeout_seconds=float(raw.get("timeout_seconds", 30.0)),
                default_effect=ToolEffect(str(raw.get("default_effect") or ToolEffect.SENSITIVE.value)),
                tool_effects=tuple(
                    (str(tool), ToolEffect(str(effect))) for tool, effect in raw_effects.items()
                ),
                exposure=ToolExposure(str(raw.get("exposure") or ToolExposure.DIRECT.value)),
            )
        )
    return tuple(configs)


__all__ = [
    "MCPClientManager",
    "MCPConfigurationError",
    "MCPRuntime",
    "MCPServerConfig",
    "MCPToolDescriptor",
    "MCPUnavailableError",
    "canonical_mcp_tool_name",
    "load_mcp_server_configs",
    "mcp_sdk_available",
]
