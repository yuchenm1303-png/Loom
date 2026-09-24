from __future__ import annotations

import threading

from .memory_configured_runtime import ConfiguredSemanticMemoryRuntime
from .tools import ToolRegistry
from .web_search import (
    WebSearchProvider,
    web_search_provider_from_env,
    web_search_provider_from_values,
)
from .web_search_tools import web_search_tools

_WEB_SEARCH_TOOL_NAMES = frozenset({"web_search", "web_search_status"})


class _DefaultSemanticMemoryRuntime(ConfiguredSemanticMemoryRuntime):
    """Cost-aware Memory v2-B policy for Loom's default runtime stack."""

    def _queue_semantic_result(self, result) -> None:
        pipeline = self._semantic_pipeline
        if (
            pipeline is None
            or not self.memory_enabled
            or not self.memory_semantic_auto
            or not result.consolidated
        ):
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
        self.web_search_guard = threading.RLock()
        self.web_search_provider_choice = "auto"
        self.web_search_key_source = "none"
        self.web_search_error = ""
        self.web_search_timeout_seconds = 20.0
        super().__init__(*args, **kwargs)
        provider = web_search_provider
        if provider is None and auto_configure_web_search:
            try:
                provider = web_search_provider_from_env()
            except ValueError as exc:
                # A half-configured environment (for example a generic key with
                # no provider) must leave dispatch, but it must not take the
                # whole runtime down. Record why, so the settings page can show
                # "Error" instead of a silent "Not configured".
                provider = None
                self.web_search_error = str(exc)
        self.web_search_provider = provider
        if provider is not None and provider.provider_name in {"brave", "tavily"}:
            self.web_search_key_source = "environment"
        self._install_web_search_tools()

    def _install_web_search_tools(self) -> None:
        """(Re)register the web search family for the current provider.

        Rebuilding the registry is what makes a settings-page provider change
        take effect without restarting the App Server: ``web_search`` appears or
        disappears, and a newly stored credential replaces the old handler.
        """

        keep = [tool for tool in self.tools.all() if tool.name not in _WEB_SEARCH_TOOL_NAMES]
        keep.extend(web_search_tools(self.web_search_provider))
        self.tools = ToolRegistry(tuple(keep))

    def web_search_install_provider(
        self,
        provider: WebSearchProvider | None,
        *,
        choice: str = "auto",
        key_source: str = "none",
        error: str = "",
    ) -> dict[str, object]:
        """Install an already-built provider and refresh tool exposure."""

        with self.web_search_guard:
            self.web_search_provider = provider
            self.web_search_provider_choice = str(choice or "auto").strip().casefold() or "auto"
            self.web_search_key_source = str(key_source or "none").strip() or "none"
            self.web_search_error = str(error or "")
            self._install_web_search_tools()
        return self.web_search_status()

    def web_search_configure(
        self,
        *,
        provider: str = "auto",
        api_key: str = "",
        timeout_seconds: float | None = None,
        key_source: str = "",
    ) -> dict[str, object]:
        """Build a provider from an explicit name/key and install it.

        ``api_key`` is consumed here and stored only inside the provider object;
        it never reaches the registry, a Session, or a tool result.
        """

        resolved_timeout = (
            self.web_search_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        built = web_search_provider_from_values(
            provider,
            api_key,
            timeout_seconds=resolved_timeout,
        )
        source = str(key_source or "").strip()
        if not source:
            source = "keyring" if str(api_key or "").strip() else "none"
        return self.web_search_install_provider(built, choice=provider, key_source=source)

    def web_search_status(self) -> dict[str, object]:
        provider = self.web_search_provider
        choice = self.web_search_provider_choice
        enabled = provider is not None
        if enabled:
            state = "error" if self.web_search_error else "ready"
        elif choice in {"off", "none", "disabled"}:
            state = "disabled"
        else:
            state = "not_configured"
        return {
            "enabled": enabled,
            "configured": enabled,
            "provider": provider.provider_name if provider is not None else "disabled",
            "choice": choice,
            "keySource": self.web_search_key_source if enabled else "none",
            "state": state,
            "reason": self.web_search_error,
        }


__all__ = ["WebSearchRuntime"]
