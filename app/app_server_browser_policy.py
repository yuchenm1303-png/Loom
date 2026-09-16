from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

from app.agent_runtime import PermissionMode

from .app_server_project_move import (
    ProjectMovableJsonRpcStdioServer,
    ProjectMovableLoomAppServerService,
    ProjectMovableLoomRpcController,
)


class BrowserPolicyLoomAppServerService(ProjectMovableLoomAppServerService):
    """Production App Server browser wiring with explicit backend selection."""

    def _apply_capability_settings(self, settings: dict[str, Any]) -> None:
        super()._apply_capability_settings(settings)
        # Capability toggles rebuild ToolRegistry from the canonical snapshot.
        # Restore the selected backend's browser_open description afterwards so
        # the model is never told it is driving a different browser route.
        rewrite = getattr(self.runtime, "_rewrite_browser_open_description", None)
        if callable(rewrite):
            rewrite()
        install_registry = getattr(self.runtime, "_install_browser_backends_tool", None)
        if callable(install_registry):
            install_registry()

    def _apply_browser_settings(self, settings: dict[str, Any]) -> str:
        """Apply exactly the browser backend the user selected.

        Current browser, isolated browser, and developer CDP are separate
        backends. A failure to use one is surfaced to the caller; the App Server
        never issues a hidden request for another backend.
        """

        apply = getattr(self.runtime, "browser_set_connection", None)
        if not callable(apply):
            return ""
        raw = settings.get("browser")
        preferences = dict(raw) if isinstance(raw, dict) else {}
        mode = str(preferences.get("mode") or "current-browser").strip()
        engine = str(preferences.get("preferredEngine") or "").strip()
        persist = preferences.get("persistSessions")
        if hasattr(self.runtime, "browser_model_controlled_connection"):
            self.runtime.browser_model_controlled_connection = bool(
                preferences.get("modelSelectsConnection", False)
            )
        private = getattr(self.runtime, "browser_set_private_networks", None)
        if callable(private):
            try:
                private(bool(preferences.get("allowPrivateNetworks", False)))
            except Exception:
                pass
        try:
            apply(
                mode,
                cdp_url=str(preferences.get("cdpUrl") or "").strip(),
                persist_profile=None if persist is None else bool(persist),
                engine=engine,
            )
            return ""
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _hud_tool_family(tool_name: str) -> str:
        """The desktop overlay belongs to Computer Use, never DOM automation.

        The current-tab extension draws a real page-local target around DOM
        elements. Browser events must never reuse the virtual-desktop Computer
        HUD or invent screen coordinates from element indexes.
        """

        name = str(tool_name or "").strip()
        return "computer" if name.startswith("computer_") else ""

    @classmethod
    def _hud_point(
        cls,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[float, float] | None:
        if str(tool_name or "").startswith("browser_"):
            return None
        return super()._hud_point(tool_name, args, result)


class BrowserPolicyLoomRpcController(ProjectMovableLoomRpcController):
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        hud = dict(result.get("capabilities", {}).get("automationHud") or {})
        hud["sources"] = ["computer"]
        hud["browserPresentation"] = "page-local-extension"
        result["capabilities"]["automationHud"] = hud
        return result


class BrowserPolicyJsonRpcStdioServer(ProjectMovableJsonRpcStdioServer):
    def __init__(self, service: BrowserPolicyLoomAppServerService, **kwargs: Any) -> None:
        super().__init__(service, **kwargs)
        self.controller = BrowserPolicyLoomRpcController(service)


def serve_browser_policy_managed_streaming_stdio(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    vision: bool = True,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    setattr(runtime, "supports_vision", bool(vision))
    service = BrowserPolicyLoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
        vision=bool(vision),
    )
    server = BrowserPolicyJsonRpcStdioServer(service)
    try:
        return server.serve(reader=reader, writer=writer)
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = [
    "BrowserPolicyJsonRpcStdioServer",
    "BrowserPolicyLoomAppServerService",
    "BrowserPolicyLoomRpcController",
    "serve_browser_policy_managed_streaming_stdio",
]
