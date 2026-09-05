from __future__ import annotations

from .memory_runtime import MemoryRuntime
from .web_search import WebSearchProvider, web_search_provider_from_env
from .web_search_tools import web_search_tools


class WebSearchRuntime(MemoryRuntime):
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
