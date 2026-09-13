from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from . import mcp_runtime as _mcp_runtime
from .computer_driver_runtime import ComputerDriverRuntime
from .mcp_runtime import MCPRuntime, MCPServerConfig
from .step import StepContext


class ConfiguredMCPRuntime(ComputerDriverRuntime, MCPRuntime):
    """Default Loom runtime with mature Computer Driver plus MCP discovery.

    ``ComputerDriverRuntime`` keeps the historical low-level Computer Use stack as
    fallback while routing full desktop tasks through a provider-neutral mature
    driver boundary. ``MCPRuntime`` remains a sibling BrowserRuntime layer. The
    cooperative MRO deliberately composes them here so Loom keeps one canonical
    Agent drive loop while the default stack gains Computer Use before Tool Search,
    Skills, Code Mode and Streaming. Embedders that intentionally instantiate the
    lower-level ``MCPRuntime`` continue to get the historical MCP-only layer.

    Embedders can pass ``mcp_servers`` explicitly. The CLI does not need MCP-specific
    wiring: when omitted, Loom reads ``$LOOM_CONFIG`` or ``<runtime-home>/config.toml``.
    A missing file means MCP is disabled.
    """

    def __init__(
        self,
        *args: Any,
        mcp_servers: Sequence[MCPServerConfig] | None = None,
        mcp_config_path: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_servers = mcp_servers
        self.mcp_config_path = ""
        if resolved_servers is None:
            store = kwargs.get("store")
            if store is None:
                raise ValueError("default MCP config discovery requires the Runtime store")
            root = Path(getattr(store, "root", "")).expanduser().resolve()
            # FileAgentSessionStore.root = <runtime-home>/agent_runtime/sessions.
            try:
                runtime_home = root.parents[1]
            except IndexError as exc:
                raise ValueError("cannot derive Loom runtime home from the session store") from exc
            selected = Path(
                mcp_config_path
                or os.environ.get("LOOM_CONFIG")
                or (runtime_home / "config.toml")
            ).expanduser().resolve()
            self.mcp_config_path = str(selected)
            # Resolved through the module, not a name bound at import time.
            # `runtime_capability_defaults` installs the JSON-aware loader by
            # rebinding `mcp_runtime.load_mcp_server_configs`; a `from ... import`
            # here would keep pointing at the original TOML-only function and
            # send a Claude Desktop / Cursor `.json` config straight into
            # `tomllib.loads`.
            resolved_servers = _mcp_runtime.load_mcp_server_configs(selected)
        elif mcp_config_path is not None:
            self.mcp_config_path = str(Path(mcp_config_path).expanduser().resolve())

        super().__init__(*args, mcp_servers=tuple(resolved_servers or ()), **kwargs)

    @staticmethod
    def _identity_hash(value: object) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    def _mcp_binding_snapshot(self) -> dict[str, object]:
        status = dict(super().mcp_status())
        status_by_name = {
            str(item.get("name") or ""): item
            for item in status.get("servers", [])
            if isinstance(item, dict)
        }
        servers = []
        for config in sorted(self.mcp_clients.configs, key=lambda item: item.name):
            current = status_by_name.get(config.name, {})
            servers.append(
                {
                    "name": config.name,
                    "transport": config.transport,
                    # Availability is intentionally excluded. A transient disconnect
                    # should make execution fail, not mutate the identity of an
                    # otherwise unchanged server binding.
                    "protocol_version": str(current.get("protocol_version") or ""),
                    "server_info": str(current.get("server_info") or ""),
                    "tool_count": int(current.get("tool_count") or 0),
                    "config_sha256": self._identity_hash(repr(config)),
                }
            )

        tools = []
        for tool in sorted(
            (item for item in self.tools.all() if item.name.startswith("mcp.")),
            key=lambda item: item.name,
        ):
            tools.append(
                {
                    "name": tool.name,
                    "effect": tool.effect.value,
                    "exposure": tool.exposure.value,
                    "binding_sha256": self._identity_hash(tool.binding_key),
                }
            )
        return {"servers": servers, "tools": tools}

    def _build_step_context(
        self,
        session,
        *,
        next_model_step: bool,
        step_id: str | None = None,
    ) -> StepContext:
        step = super()._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        if not step.request_state.captured:
            return step
        binding_json = json.dumps(
            self._mcp_binding_snapshot(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return replace(
            step,
            request_state=replace(
                step.request_state,
                mcp_binding_json=binding_json,
            ),
        )

    def mcp_status(self) -> dict[str, object]:
        status = dict(super().mcp_status())
        status["config_path"] = self.mcp_config_path
        return status


__all__ = ["ConfiguredMCPRuntime"]
