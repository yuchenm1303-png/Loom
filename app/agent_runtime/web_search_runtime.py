from __future__ import annotations

from .memory_semantic import SemanticMemoryRuntime
from .web_search import WebSearchProvider, web_search_provider_from_env
from .web_search_tools import web_search_tools


class _DefaultSemanticMemoryRuntime(SemanticMemoryRuntime):
    """Cost-aware semantic memory policy for Loom's default runtime stack."""

    def _queue_semantic_result(self, result) -> None:
        pipeline = self._semantic_pipeline
        if pipeline is None or not result.consolidated:
            return
        extraction_id = result.extraction.extraction_id
        if not self.memory_semantic_store.has_extraction_memories(extraction_id):
            return

        # A single first memory has nothing to reconcile. Avoid paying for a
        # second model request until the extraction has either multiple new
        # memories or at least one existing same-scope memory to compare with.
        bundle = self.memory_semantic_store.bundle(extraction_id, related_limit=4)
        if len(bundle.new_memories) + len(bundle.related_memories) < 2:
            return

        self.memory_semantic_store.enqueue(
            extraction_id,
            result.extraction.source_session_id,
        )
        pipeline.schedule(extraction_id, delay=0.0)


class WebSearchRuntime(_DefaultSemanticMemoryRuntime):
    """Runtime v2 layer that conditionally exposes credential-backed web search.

    Search credentials stay inside the provider object and are never added to
    Session/WorldState/tool results. External search is a SENSITIVE tool effect,
    so approval/workspace modes still ask before the network request while
    full-access can execute it directly.
    """

    def __init__(
        self,
        *args,
        web_search_provider: WebSearchProvider | None = None,
        auto_configure_web_search: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        provider = web_search_provider
        if provider is None and auto_configure_web_search:
            provider = web_search_provider_from_env()
        self.web_search_provider = provider
        for tool in web_search_tools(provider):
            if self.tools.get(tool.name) is None:
                self.tools.register(tool)

    def web_search_status(self) -> dict[str, object]:
        provider = self.web_search_provider
        return {
            "enabled": provider is not None,
            "provider": provider.provider_name if provider is not None else "disabled",
        }


__all__ = ["WebSearchRuntime"]
