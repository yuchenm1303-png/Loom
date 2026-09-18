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
      Current Tab Bridge extension. If the bridge is offline, opening that backend
      fails immediately and never silently launches another browser.
    * ``isolated`` explicitly launches Loom's own visible browser.
    * ``cdp`` is the developer/debug transport and requires a configured loopback
      DevTools endpoint.

    The model may always choose the isolated backend because it is a reduction in
    authority from a signed-in user browser. Selecting an external backend that the
    user did not configure remains opt-in through modelSelectsConnection.
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
        self._browser_requested_connection = selected
        # browser_open is allowed to choose a backend. The separate flag controls
        # privilege escalation to an external backend the user did not configure.
        self.browser_model_controlled_connection = True
        self.browser_allow_external_backend_selection = False
        self._install_browser_backends_tool()
        self._rewrite_browser_open_description()

    def _install_browser_backends_tool(self) -> None:
        def handler(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
            del context, arguments
            return ToolResult(
                ok=True,
                content="Browser backend registry.",
                data={
                    "selected_backend": self._browser_requested_backend,
                    "backends": list(self.browser_backend_registry()),
                },
            )

        tool = AgentTool(
            name="browser_backends",
            description=(
                "List Loom's browser backends and whether each is currently available/model-selectable. "
                "current-browser means the user's already running Edge/Chrome tab through the Loom Current Tab Bridge; "
                "isolated means a separate Loom-owned browser; cdp is the developer/debug transport."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=handler,
            effect=ToolEffect.READ_ONLY,
        )
        existing = self.tools.get(tool.name)
        if existing is None:
            self.tools.register(tool)
        elif existing.description != tool.description:
            self.tools = ToolRegistry(
                tuple(tool if candidate.name == tool.name else candidate for candidate in self.tools.all())
            )

    def _current_browser_bridge(self):
        bridge = getattr(self, "browser_extension_bridge", None)
        if bridge is None:
            bridge = self._extension_bridge_for_session()
        return bridge

    def _backend_model_selectable(self, backend: str) -> bool:
        selected = str(getattr(self, "_browser_requested_backend", ISOLATED_BROWSER))
        if backend == ISOLATED_BROWSER:
            return True
        if backend == selected:
            return True
        return bool(getattr(self, "browser_allow_external_backend_selection", False))

    def browser_backend_registry(self) -> tuple[dict[str, object], ...]:
        # Deliberately not _current_browser_bridge(): this feeds browser_status and
        # the read-only browser_backends tool, and reporting availability must not
        # bind a port as a side effect. The cost is that current-browser reads as
        # unavailable until something opens a bridge, while browser_open with
        # connect=current_tab would still succeed by creating one.
        bridge = getattr(self, "browser_extension_bridge", None)
        current_connected = bool(bridge is not None and bridge.connected)
        bridge_status = bridge.status() if bridge is not None else {}
        browser_use_ready = bool(browser_use_available())
        selected = str(getattr(self, "_browser_requested_backend", ISOLATED_BROWSER))
        cdp_configured = bool(str(getattr(self, "_browser_cdp_url", "") or "").strip())
        return (
            {
                "id": CURRENT_BROWSER,
                "selected": selected == CURRENT_BROWSER,
                "available": current_connected,
                "model_selectable": self._backend_model_selectable(CURRENT_BROWSER),
                "external_browser": True,
                "requires_extension": True,
                "browser": str(bridge_status.get("browser") or ""),
                "current_tab": bridge_status.get("current_tab"),
                # A port conflict is not a missing extension, and saying so sends
                # the user to reinstall something that was never broken.
                "reason": "" if current_connected else (
                    str(bridge_status.get("port_conflict") or "")
                    or "Loom Current Tab Bridge is not connected. Enable the Loom browser extension in Edge/Chrome."
                ),
            },
            {
                "id": ISOLATED_BROWSER,
                "selected": selected == ISOLATED_BROWSER,
                "available": browser_use_ready,
                "model_selectable": True,
                "external_browser": False,
                "requires_extension": False,
                "reason": "" if browser_use_ready else "browser-use runtime is unavailable",
            },
            {
                "id": DEVELOPER_CDP,
                "selected": selected == DEVELOPER_CDP,
                "available": browser_use_ready and cdp_configured,
                "model_selectable": self._backend_model_selectable(DEVELOPER_CDP),
                "external_browser": True,
                "requires_extension": False,
                "configured": cdp_configured,
                "reason": "" if browser_use_ready and cdp_configured else (
                    "Configure a loopback CDP endpoint before using the developer browser backend."
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
                "Open Browser Use through the Loom Current Tab Bridge extension. When the task names or implies a target "
                "page, always pass its http/https URL in this browser_open call: Loom will reuse an exact matching page, "
                "reuse a Loom-owned work tab, or automatically create a purple Loom tab group without overwriting the "
                "user's unrelated tab. Omit url only when the user explicitly wants the page currently visible. If that "
                "page is edge://, chrome://, or another protected page, continue with browser_navigate to the target; never "
                "ask the user to switch to an ordinary page. This preserves the user's profile, cookies, and login state. "
                "If the extension is not "
                "connected, fail immediately and tell the user to set up or enable the extension. Never change to an "
                "isolated browser or Computer Use merely because current-browser is unavailable. Use connect=launch "
                "only when the user explicitly asks for a clean, isolated, test, or signed-out browser."
            )
        elif selected == DEVELOPER_CDP:
            description = (
                "Open the explicitly configured developer Chrome/Edge connection through loopback-only CDP by default. "
                "This is a debugging transport. Failure must not change the browser backend. Use connect=launch only "
                "when the user explicitly asks for a clean, isolated, test, or signed-out browser."
            )
        else:
            description = (
                "Open a separate visible browser owned by Loom by default. This isolated backend does not claim to be the "
                "user's already-running Edge/Chrome. Switching to an external signed-in browser requires the user's "
                "model-selected browser connection permission."
            )
        if tool.description == description:
            return
        replacement = replace(tool, description=description)
        self.tools = ToolRegistry(
            tuple(replacement if candidate.name == "browser_open" else candidate for candidate in self.tools.all())
        )

    def _rewrite_auto_browser_open_description(self) -> None:
        self._rewrite_browser_open_description()

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
            self.browser_headless = False
            super().browser_set_connection(
                "extension", cdp_url="", persist_profile=persist_profile, engine=engine
            )
        elif backend == ISOLATED_BROWSER:
            self.browser_headless = False
            super().browser_set_connection(
                "local-launch", cdp_url="", persist_profile=persist_profile, engine=engine
            )
        else:
            super().browser_set_connection(
                "cdp-attach", cdp_url=cdp_url, persist_profile=persist_profile, engine=engine
            )
        self._browser_requested_backend = backend
        self._browser_requested_connection = backend
        # BrowserRuntime rewrites this flag while switching modes. Restore the
        # registry contract: model-directed backend selection is always available,
        # while external escalation is governed separately.
        self.browser_model_controlled_connection = True
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
            if selected != CURRENT_BROWSER and not self._backend_model_selectable(CURRENT_BROWSER):
                raise PermissionError(
                    "model-selected current-browser access is disabled. Enable model-selected browser connections "
                    "or choose Current browser in Settings > Browser."
                )
            bridge = self._current_browser_bridge()
            if not bridge.connected:
                raise RuntimeError(
                    "current-browser backend is unavailable: Loom Current Tab Bridge is not connected. "
                    "Open Settings > Browser and install, repair, or enable the Loom Current Tab Bridge extension. "
                    "Loom did not open another browser, will not use Computer Use as a substitute, and will not "
                    "switch backends silently."
                )
            if normalized in {"", "default"}:
                factory, _external, _label = super().browser_session_connection("", cdp_url="")
                return factory, True, CURRENT_BROWSER

            def build_extension(options):
                return BrowserExtensionSessionBackend(options=options, bridge=bridge)

            return build_extension, True, CURRENT_BROWSER

        if normalized in {"isolated", "launch"}:
            factory, _external, _label = super().browser_session_connection("launch", cdp_url="")
            return factory, False, ISOLATED_BROWSER

        wants_cdp = normalized in {"cdp", "attach"}
        if wants_cdp:
            if selected != DEVELOPER_CDP and not self._backend_model_selectable(DEVELOPER_CDP):
                raise PermissionError(
                    "model-selected developer CDP access is disabled. Choose Developer CDP in Settings > Browser "
                    "or enable model-selected browser connections."
                )
            factory, _external, _label = super().browser_session_connection("attach", cdp_url=cdp_url)
            return factory, True, DEVELOPER_CDP

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
        status["model_selects_backend"] = True
        status["external_backend_selection_allowed"] = bool(
            getattr(self, "browser_allow_external_backend_selection", False)
        )

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
