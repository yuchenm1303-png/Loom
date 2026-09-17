from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

from app.agent_runtime import AgentEventKind, PermissionMode

from .app_server_project_move import (
    ProjectMovableJsonRpcStdioServer,
    ProjectMovableLoomAppServerService,
    ProjectMovableLoomRpcController,
)


_BROWSER_HUD_TERMINAL_EVENTS = frozenset(
    {
        AgentEventKind.TURN_COMPLETED,
        AgentEventKind.TURN_FAILED,
        AgentEventKind.TURN_CANCELLED,
        AgentEventKind.TURN_INTERRUPTED,
        AgentEventKind.LIMIT_REACHED,
    }
)


class BrowserPolicyLoomAppServerService(ProjectMovableLoomAppServerService):
    """Production App Server browser wiring with explicit backend selection."""

    def _apply_capability_settings(self, settings: dict[str, Any]) -> None:
        super()._apply_capability_settings(settings)
        rewrite = getattr(self.runtime, "_rewrite_browser_open_description", None)
        if callable(rewrite):
            rewrite()
        install_registry = getattr(self.runtime, "_install_browser_backends_tool", None)
        if callable(install_registry):
            install_registry()

    def _apply_browser_settings(self, settings: dict[str, Any]) -> str:
        """Apply exactly the browser backend the user selected.

        The model may always explicitly choose Loom's isolated browser because
        that reduces authority compared with a signed-in user browser. The
        ``modelSelectsConnection`` setting now controls escalation to a different
        external backend (current-browser/CDP), not whether the model can open a
        clean isolated session. No backend is ever selected silently.
        """

        apply = getattr(self.runtime, "browser_set_connection", None)
        if not callable(apply):
            return ""
        raw = settings.get("browser")
        preferences = dict(raw) if isinstance(raw, dict) else {}
        mode = str(preferences.get("mode") or "auto").strip()
        engine = str(preferences.get("preferredEngine") or "").strip()
        persist = preferences.get("persistSessions")

        if hasattr(self.runtime, "browser_model_controlled_connection"):
            # Keep browser_open's per-session backend selector available. The
            # registry itself enforces whether a requested external backend is an
            # allowed escalation.
            self.runtime.browser_model_controlled_connection = True
        if hasattr(self.runtime, "browser_allow_external_backend_selection"):
            self.runtime.browser_allow_external_backend_selection = bool(
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
            # BrowserRuntime may rewrite its legacy flag while rebuilding a
            # transport; restore the backend-registry policy afterwards.
            if hasattr(self.runtime, "browser_model_controlled_connection"):
                self.runtime.browser_model_controlled_connection = True
            return ""
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    def _on_runtime_event(self, event: Any) -> None:
        super()._on_runtime_event(event)
        if getattr(event, "kind", None) not in _BROWSER_HUD_TERMINAL_EVENTS:
            return

        # Browser sessions intentionally survive a turn so the next user message
        # can continue with the same tab, login state, and browser_id. The visual
        # session indicator must not survive the turn, though. Tell the Current
        # Tab Bridge to fade every page-local HUD after the terminal event rather
        # than closing the browser or relying on an arbitrary idle timeout.
        bridge = getattr(self.runtime, "browser_extension_bridge", None)
        if bridge is None or not bool(getattr(bridge, "connected", False)):
            return
        try:
            bridge.call("hud_end", {"turn_id": str(getattr(event, "turn_id", "") or "")}, timeout=2.0)
        except Exception:
            # Turn completion must never fail merely because Edge closed or the
            # extension disconnected while the final response was being emitted.
            pass

    @staticmethod
    def _hud_tool_family(tool_name: str) -> str:
        """The desktop overlay belongs to Computer Use, never DOM automation."""

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
