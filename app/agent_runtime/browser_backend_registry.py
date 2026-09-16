from __future__ import annotations

from dataclasses import replace
from typing import Any

from .browser_extension_bridge import BrowserExtensionSessionBackend
from .browser_use_backend import browser_use_available
from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolRegistry, ToolResult


CURRENT_BROWSER = "current-browser"
ISOLATED_BROWSER = "isolated"
DEVELOPER_CDP = "cdp"

_BROWSER_BACKEND_ALIASES = {
    "auto": CURRENT_BROWSER,
    "current-browser": CURRENT_BROWSER,
    "current_browser": CURRENT_BROWSER,
    "current-tab": CURRENT_BROWSER,
    "current_tab": CURRENT_BROWSER,
    "extension": CURRENT_BROWSER,
    "isolated": ISOLATED_BROWSER,
    "local-launch": ISOLATED_BROWSER,
    "local_launch": ISOLATED_BROWSER,
    "launch": ISOLATED_BROWSER,
    "cdp": DEVELOPER_CDP,
    "cdp-attach": DEVELOPER_CDP,
    "cdp_attach": DEVELOPER_CDP,
    "attach": DEVELOPER_CDP,
}


def normalize_browser_backend(value: str) -> str:
    key = str(value or "").strip().casefold()
    try:
        return _BROWSER_BACKEND_ALIASES[key]
    except KeyError as exc:
        raise ValueError(
            "browser backend must be one of: current-browser, isolated, cdp"
        ) from exc


class BrowserBackendRegistryMixin:
    """Codex-style browser backend registry for Loom's existing transports.

    BrowserRuntime owns the transport implementations. This mixin gives them
    product-level identities and availability semantics instead of hiding several
    transports behind an ``auto`` fallback:

    * ``current-browser`` drives the user's active Edge/Chrome tab through the
      Current Tab Bridge extension. If the bridge is offline, opening a session
      fails immediately and never launches another browser.
    * ``isolated`` explicitly launches Loom's own visible browser.
    * ``cdp`` is the developer/debug transport and requires a configured loopback
      DevTools endpoint.

    Legacy setting names remain accepted as aliases so existing installations can
    migrate without changing what the user selected.
    """

    _browser_requested_backend: str = ISOLATED_BROWSER

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if bool(getattr(self, "browser_extension_attached", False)):
            selected = CURRENT_BROWSER
        elif bool(getattr(self, "browser_cdp_attached", False)):
            selected = DEVELOPER_CDP
        else:
            selected = ISOLATED_BROWSER
        self._browser_requested_backend = selected
        # Kept for compatibility with older diagnostics/tests that read this
        # private attribute. Its value is now the canonical backend id.
        self._browser_requested_connection = selected
        self._install_browser_backends_tool()
        self._rewrite_browser_open_description()

    def _install_browser_backends_tool(self) -> None:
        def handler(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
            del context, arguments
            backends = list(self.browser_backend_registry())
            return ToolResult(
                ok=True,
                content="Browser backend registry.",
                data={
                    "selected_backend": self._browser_requested_backend,
                    "backends": backends,
                },
            )

        tool = AgentTool(
            name="browser_backends",
            description=(
                "List Loom's browser backends and whether each is currently available. "
                "Use this before changing browser routes. current-browser means the user's "
                "already running Edge/Chrome tab through the Loom Current Tab Bridge; isolated "
                "means a separate Loom-owned browser; cdp is the developer/debug transport."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=handler,
            effect=ToolEffect.READ_ONLY,
        )
        existing = self.tools.get(tool.name)
        if existing is None:
            self.tools.register(tool)
        elif existing != tool:
            self.tools = ToolRegistry(
                tuple(tool if candidate.name == tool.name else candidate for candidate in self.tools.all())
            )

    def _current_browser_bridge(self):
        bridge = getattr(self, "browser_extension_bridge", None)
        if bridge is None:
            bridge = self._extension_bridge_for_session()
        return bridge

    def browser_backend_registry(self) -> tuple[dict[str, object], ...]:
        bridge = getattr(self, "browser_extension_bridge", None)
        current_connected = bool(bridge is not None and bridge.connected)
        browser_use_ready = bool(browser_use_available())
        selected = str(getattr(self, "_browser_requested_backend", ISOLATED_BROWSER))
        cdp_configured = bool(str(getattr(self, "_browser_cdp_url", "") or "").strip())
        return (
            {
                "id": CURRENT_BROWSER,
                "selected": selected == CURRENT_BROWSER,
                "available": current_connected,
                "external_browser": True,
                "requires_extension": True,
                "reason": (
                    ""
                    if current_connected
                    else "Loom Current Tab Bridge is not connected. Enable the Loom browser extension in Edge/Chrome."
                ),
            },
            {
                "id": ISOLATED_BROWSER,
                "selected": selected == ISOLATED_BROWSER,
                "available": browser_use_ready,
                "external_browser": False,
                "requires_extension": False,
                "reason": "" if browser_use_ready else "browser-use runtime is unavailable",
            },
            {
                "id": DEVELOPER_CDP,
                "selected": selected == DEVELOPER_CDP,
                "available": browser_use_ready and cdp_configured,
                "external_browser": True,
                "requires_extension": False,
                "configured": cdp_configured,
                "reason": (
                    ""
                    if browser_use_ready and cdp_configured
                    else "Configure a loopback CDP endpoint before using the developer browser backend."
                ),
            },
        )

    def _rewrite_browser_open_description(self) -> None:
        tool = self.tools.get("browser_open")
        if tool is None:
            return
        selected = str(getattr(self, "_browser_requested_backend", ISOLATED_BROWSER))
        if selected == CURRENT_BROWSER:
            description = (
                "Open the user's current Edge/Chrome tab through the Loom Current Tab Bridge extension. "
                "This backend preserves the user's existing profile, cookies, login state, tabs, and page-local "
                "Browser HUD. If the extension is not connected, fail immediately; never open an isolated fallback. "
                "The returned browser_connection states the real backend used."
            )
        elif selected == DEVELOPER_CDP:
            description = (
                "Open the explicitly configured developer Chrome/Edge connection through loopback-only CDP. "
                "This is a debugging transport, not Loom's normal current-browser route. Closing Loom disconnects "
                "without terminating the user's browser."
            )
        else:
            description = (
                "Open a separate visible browser owned by Loom. This isolated backend does not claim to be the user's "
                "already-running Edge/Chrome and does not inherit that browser's login state unless Loom's own persistent "
                "profile already contains it."
            )
        if tool.description == description:
            return
        replacement = replace(tool, description=description)
        self.tools = ToolRegistry(
            tuple(replacement if candidate.name == "browser_open" else candidate for candidate in self.tools.all())
        )

    def browser_set_connection(
        self,
        mode: str,
        *,
        cdp_url: str = "",
        persist_profile: bool | None = None,
        engine: str = "",
    ) -> dict[str, object]:
        backend = normalize_browser_backend(mode)
        if backend == CURRENT_BROWSER:
            # Configure the extension transport even before a browser client has
            # connected. That keeps the bridge listening so enabling the extension
            # later makes the selected backend available without restarting Loom.
            self.browser_headless = False
            super().browser_set_connection(
                "extension",
                cdp_url="",
                persist_profile=persist_profile,
                engine=engine,
            )
        elif backend == ISOLATED_BROWSER:
            self.browser_headless = False
            super().browser_set_connection(
                "local-launch",
                cdp_url="",
                persist_profile=persist_profile,
                engine=engine,
            )
        else:
            super().browser_set_connection(
                "cdp-attach",
                cdp_url=cdp_url,
                persist_profile=persist_profile,
                engine=engine,
            )
        self._browser_requested_backend = backend
        self._browser_requested_connection = backend
        self._install_browser_backends_tool()
        self._rewrite_browser_open_description()
        return self.browser_status()

    def browser_session_connection(self, connect: str, *, cdp_url: str = ""):
        normalized = str(connect or "").strip().casefold().replace("-", "_")
        selected = str(getattr(self, "_browser_requested_backend", ISOLATED_BROWSER))

        wants_current = normalized in {"current_tab", "current_browser"}
        if normalized in {"", "default"} and selected == CURRENT_BROWSER:
            wants_current = True
        if wants_current:
            bridge = self._current_browser_bridge()
            if not bridge.connected:
                raise RuntimeError(
                    "current-browser backend is unavailable: Loom Current Tab Bridge is not connected. "
                    "Enable the Loom browser extension in Edge/Chrome, then retry. Loom will not open another browser automatically."
                )
            if normalized in {"", "default"}:
                return super().browser_session_connection("", cdp_url="")

            def build_extension(options):
                return BrowserExtensionSessionBackend(options=options, bridge=bridge)

            return build_extension, True, "current-browser"

        if normalized in {"isolated", "launch"}:
            return super().browser_session_connection("launch", cdp_url="")
        if normalized in {"cdp", "attach"}:
            return super().browser_session_connection("attach", cdp_url=cdp_url)
        return super().browser_session_connection(connect, cdp_url=cdp_url)

    def browser_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        status = dict(super().browser_status(owner_session_id))
        selected = str(getattr(self, "_browser_requested_backend", ISOLATED_BROWSER))
        registry = list(self.browser_backend_registry())
        selected_row = next((row for row in registry if row.get("id") == selected), None) or {}
        available = bool(selected_row.get("available"))
        status["requested_browser_connection"] = selected
        status["selected_browser_backend"] = selected
        status["selected_backend_available"] = available
        status["browser_backends"] = registry
        status["headless"] = bool(getattr(self, "browser_headless", False))
        status["automatic_fallback"] = False

        if selected == CURRENT_BROWSER:
            status["browser_connection"] = "extension-bridge" if available else "current-browser-unavailable"
            status["backend"] = "browser-extension"
            status["external_browser"] = available
            status["session_persistence"] = "extension-current-browser"
            status["crash_recovery"] = "extension_reconnects_to_local_bridge"
            status["storage_state_persistence"] = True
            status["profile_name"] = "current-browser-extension"
            status["connection_error"] = str(selected_row.get("reason") or "")
        else:
            status["connection_error"] = "" if available else str(selected_row.get("reason") or "")
        return status


__all__ = [
    "BrowserBackendRegistryMixin",
    "CURRENT_BROWSER",
    "DEVELOPER_CDP",
    "ISOLATED_BROWSER",
    "normalize_browser_backend",
]
