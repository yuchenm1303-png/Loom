from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock


@dataclass(frozen=True, slots=True)
class McpToolApprovalKey:
    """Codex session/persistent MCP approval identity.

    Tool arguments are intentionally not part of this key.  The key names the
    configured server/tool plus app/connector provenance used by Codex.
    """

    server: str
    plugin_id: str | None
    connector_id: str | None
    link_id: str | None
    tool_name: str

    def __post_init__(self) -> None:
        server = str(self.server or "").strip()
        tool_name = str(self.tool_name or "").strip()
        if not server:
            raise ValueError("MCP approval server must not be empty")
        if not tool_name:
            raise ValueError("MCP approval tool_name must not be empty")
        object.__setattr__(self, "server", server)
        object.__setattr__(self, "tool_name", tool_name)
        for field_name in ("plugin_id", "connector_id", "link_id"):
            value = getattr(self, field_name)
            if value is not None:
                normalized = str(value).strip()
                object.__setattr__(self, field_name, normalized or None)


@dataclass(slots=True)
class McpToolApprovalSessionCache:
    """Dedicated ApprovedForSession store for MCP tool approvals."""

    _approved: set[McpToolApprovalKey] = field(default_factory=set, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def contains(self, key: McpToolApprovalKey) -> bool:
        with self._lock:
            return key in self._approved

    def approve_for_session(self, key: McpToolApprovalKey) -> None:
        with self._lock:
            self._approved.add(key)

    def clear(self) -> None:
        with self._lock:
            self._approved.clear()


__all__ = ["McpToolApprovalKey", "McpToolApprovalSessionCache"]
