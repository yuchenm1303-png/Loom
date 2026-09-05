from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from .mcp_runtime import MCPRuntime, MCPServerConfig, load_mcp_server_configs


class ConfiguredMCPRuntime(MCPRuntime):
    """Default Loom runtime that discovers operator MCP config from the runtime home.

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
            resolved_servers = load_mcp_server_configs(selected)
        elif mcp_config_path is not None:
            self.mcp_config_path = str(Path(mcp_config_path).expanduser().resolve())

        super().__init__(*args, mcp_servers=tuple(resolved_servers or ()), **kwargs)

    def mcp_status(self) -> dict[str, object]:
        status = dict(super().mcp_status())
        status["config_path"] = self.mcp_config_path
        return status


__all__ = ["ConfiguredMCPRuntime"]
