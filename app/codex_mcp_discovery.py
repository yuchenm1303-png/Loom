from __future__ import annotations

"""Safely reuse MCP servers already configured by Codex.

Codex stores its user config in ``$CODEX_HOME/config.toml`` (normally
``~/.codex/config.toml``). Loom previously discovered Claude Desktop and Cursor
but skipped Codex itself. This adapter adds Codex to discovery while preserving
Loom's stricter secret boundary: literal credential material is never copied
from another application's config into Loom's runtime model.
"""

import importlib.abc
import importlib.machinery
import os
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from app.import_patch_chain import find_spec_without


_TARGET_MODULE = "app.agent_runtime.mcp_runtime"
_INSTALLED = False
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ENV_REFERENCE_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]{0,127})\}?$")


def _codex_config_path() -> Path:
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else (Path.home() / ".codex")
    return (root / "config.toml").expanduser().resolve()


def _has_codex_mcp_servers(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Keep discovery best-effort. If this is the only candidate, Codex can
        # still diagnose its own malformed config; Loom should not steal startup.
        return False
    servers = data.get("mcp_servers")
    return isinstance(servers, dict) and bool(servers)


def _is_codex_config(path: str | Path) -> bool:
    try:
        return Path(path).expanduser().resolve() == _codex_config_path()
    except OSError:
        return False


def _env_reference(value: Any) -> str:
    text = str(value or "").strip()
    match = _ENV_REFERENCE_RE.fullmatch(text)
    return match.group(1) if match else ""


def load_codex_mcp_server_configs(module: Any, path: str | Path) -> tuple[Any, ...]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return ()
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise module.MCPConfigurationError(f"could not parse Codex MCP config: {exc}") from exc
    raw_servers = data.get("mcp_servers") or {}
    if not isinstance(raw_servers, dict):
        raise module.MCPConfigurationError("Codex [mcp_servers] must be a TOML table")

    configs: list[Any] = []
    for name, raw in raw_servers.items():
        if not isinstance(raw, dict):
            raise module.MCPConfigurationError(f"Codex MCP server {name!r} must be a table")
        enabled = bool(raw.get("enabled", True))
        if not enabled:
            # Preserve disabled entries semantically without requiring Loom to
            # understand credentials for a server that will never be started.
            command = str(raw.get("command") or "").strip()
            url = str(raw.get("url") or "").strip()
            if not command and not url:
                continue

        command = str(raw.get("command") or "").strip()
        url = str(raw.get("url") or "").strip()
        transport = "stdio" if command else "http"
        if not command and not url:
            raise module.MCPConfigurationError(
                f"Codex MCP server {name!r} must configure command or url"
            )
        args = raw.get("args") or []
        if not isinstance(args, list):
            raise module.MCPConfigurationError(f"Codex MCP server {name!r} args must be an array")

        env_from: list[tuple[str, str]] = []
        env_vars = raw.get("env_vars") or []
        if not isinstance(env_vars, list) or not all(isinstance(item, str) for item in env_vars):
            raise module.MCPConfigurationError(f"Codex MCP server {name!r} env_vars must be an array of names")
        for variable in env_vars:
            variable = variable.strip()
            if not _ENV_NAME_RE.fullmatch(variable):
                raise module.MCPConfigurationError(
                    f"Codex MCP server {name!r} contains an invalid env_vars name"
                )
            env_from.append((variable, variable))

        raw_env = raw.get("env") or {}
        if not isinstance(raw_env, dict):
            raise module.MCPConfigurationError(f"Codex MCP server {name!r} env must be a table")
        for child, value in raw_env.items():
            child_name = str(child or "").strip()
            source_name = _env_reference(value)
            if not _ENV_NAME_RE.fullmatch(child_name):
                raise module.MCPConfigurationError(
                    f"Codex MCP server {name!r} contains an invalid env variable name"
                )
            if not source_name:
                raise module.MCPConfigurationError(
                    f"Codex MCP server {name!r} has a literal env value for {child_name!r}. "
                    "Loom will not import literal values from another app's config; use Codex env_vars "
                    "or an environment reference such as '$TOKEN'."
                )
            env_from.append((child_name, source_name))

        # Loom's current MCP transport intentionally supports only bearer-token
        # auth, not arbitrary HTTP headers. Refuse rather than silently dropping
        # security-critical config or copying literal secrets.
        if raw.get("http_headers"):
            raise module.MCPConfigurationError(
                f"Codex MCP server {name!r} uses literal http_headers, which Loom will not import"
            )
        if raw.get("env_http_headers"):
            raise module.MCPConfigurationError(
                f"Codex MCP server {name!r} uses env_http_headers, which Loom's current MCP transport does not model"
            )

        bearer = str(
            raw.get("bearer_token_env_var")
            or raw.get("bearer_token_env")
            or ""
        ).strip()
        if bearer and not _ENV_NAME_RE.fullmatch(bearer):
            raise module.MCPConfigurationError(
                f"Codex MCP server {name!r} has an invalid bearer token environment variable"
            )

        timeout = raw.get("tool_timeout_sec")
        if timeout in {None, ""}:
            timeout = raw.get("startup_timeout_sec")
        if timeout in {None, ""}:
            timeout = 30.0

        configs.append(
            module.MCPServerConfig(
                name=str(name),
                transport=transport,
                command=command,
                args=tuple(str(item) for item in args),
                cwd=str(raw.get("cwd") or ""),
                url=url,
                env_from=tuple(dict.fromkeys(env_from)),
                bearer_token_env=bearer,
                enabled=enabled,
                required=False,
                timeout_seconds=float(timeout),
                default_effect=module.ToolEffect.SENSITIVE,
                exposure=module.ToolExposure.DIRECT,
            )
        )
    return tuple(configs)


def _patch_runtime_defaults() -> None:
    defaults = sys.modules.get("app.runtime_capability_defaults")
    if defaults is None:
        return
    original = getattr(defaults, "_mcp_config_paths", None)
    if not callable(original) or getattr(original, "_loom_codex_discovery", False):
        return

    def mcp_config_paths(runtime_home: str | Path = "") -> Iterable[Path]:
        existing = list(original(runtime_home))
        codex = _codex_config_path()
        if not _has_codex_mcp_servers(codex):
            return tuple(existing)
        # Loom's explicit own config stays first. Codex comes next, before
        # opportunistic Claude/Cursor adoption, because the product is built to
        # interoperate with the Codex configuration the user already chose.
        insert_at = 0
        explicit = str(os.environ.get("LOOM_CONFIG") or "").strip()
        if explicit and existing:
            insert_at = 1
        elif runtime_home:
            home = Path(runtime_home).expanduser().resolve()
            own = {home / "config.toml", home / "mcp.json"}
            while insert_at < len(existing) and existing[insert_at] in own:
                insert_at += 1
        if codex not in existing:
            existing.insert(insert_at, codex)
        return tuple(existing)

    mcp_config_paths._loom_codex_discovery = True  # type: ignore[attr-defined]
    defaults._mcp_config_paths = mcp_config_paths


def patch(module: Any) -> None:
    if getattr(module, "_loom_codex_mcp_loader", False):
        return
    original = module.load_mcp_server_configs

    def load_mcp_server_configs(path: str | Path):  # noqa: ANN001
        if _is_codex_config(path):
            return load_codex_mcp_server_configs(module, path)
        return original(path)

    module.load_mcp_server_configs = load_mcp_server_configs
    module._loom_codex_mcp_loader = True


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _CodexMcpLoader(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader) -> None:
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.loader, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        execute = getattr(self.loader, "exec_module", None)
        if not callable(execute):
            raise ImportError(f"loader for {_TARGET_MODULE} cannot execute modules")
        execute(module)
        patch(module)


class _CodexMcpFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _CodexMcpLoader):
            return spec
        spec.loader = _CodexMcpLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_runtime_defaults()
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _CodexMcpFinder())
    _INSTALLED = True


__all__ = ["install", "load_codex_mcp_server_configs", "patch"]
