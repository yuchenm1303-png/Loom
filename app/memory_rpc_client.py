from __future__ import annotations

from typing import Any, Protocol


class RpcRequester(Protocol):
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...


class MemoryRpcClient:
    """Typed Memory v2 calls over any Loom-compatible RPC client.

    Memory management stays available when model-side memory is disabled so the
    user can always inspect and forget already stored records.
    """

    def __init__(self, client: RpcRequester) -> None:
        if not callable(getattr(client, "request", None)):
            raise TypeError("client must expose request(method, params)")
        self.client = client

    def status(self, thread_id: str) -> dict[str, Any]:
        return dict(
            self.client.request(
                "memory/status",
                {"threadId": str(thread_id)},
            )
        )

    def list(self, thread_id: str, *, limit: int = 100) -> dict[str, Any]:
        return dict(
            self.client.request(
                "memory/list",
                {"threadId": str(thread_id), "limit": int(limit)},
            )
        )

    def search(
        self,
        thread_id: str,
        query: str,
        *,
        limit: int = 8,
    ) -> dict[str, Any]:
        return dict(
            self.client.request(
                "memory/search",
                {
                    "threadId": str(thread_id),
                    "query": str(query),
                    "limit": int(limit),
                },
            )
        )

    def read(
        self,
        thread_id: str,
        memory_id: str,
        *,
        evidence_limit: int = 20,
    ) -> dict[str, Any]:
        return dict(
            self.client.request(
                "memory/read",
                {
                    "threadId": str(thread_id),
                    "memoryId": str(memory_id),
                    "evidenceLimit": int(evidence_limit),
                },
            )
        )

    def forget(self, thread_id: str, memory_id: str) -> dict[str, Any]:
        return dict(
            self.client.request(
                "memory/forget",
                {
                    "threadId": str(thread_id),
                    "memoryId": str(memory_id),
                },
            )
        )

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        return self._set("memory.enabled", bool(enabled))

    def set_auto_extract(self, enabled: bool) -> dict[str, Any]:
        return self._set("memory.autoExtract", bool(enabled))

    def set_semantic_auto(self, enabled: bool) -> dict[str, Any]:
        return self._set("memory.semanticAuto", bool(enabled))

    def set_idle_seconds(self, seconds: int) -> dict[str, Any]:
        return self._set("memory.idleSeconds", int(seconds))

    def _set(self, path: str, value: object) -> dict[str, Any]:
        return dict(
            self.client.request(
                "settings/set",
                {"path": path, "value": value},
            )
        )


__all__ = ["MemoryRpcClient", "RpcRequester"]
