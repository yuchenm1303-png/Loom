from __future__ import annotations

from dataclasses import replace

from .browser_extension_bridge import BrowserExtensionSessionBackend
from .tools import ToolRegistry


_AUTO_MODE = "auto"


class BrowserAutoPolicyMixin:
    """Prefer the user's current browser without pretending a fallback is attached.

    ``BrowserRuntime`` already supports three concrete transports: Loom-owned
    launch, CDP attachment, and the current-tab extension bridge. This mixin adds
    a product policy on top of those transports rather than inventing a fourth
    backend:

    * ``auto`` keeps the extension bridge listening and uses it for the next
      session whenever a Chrome/Edge extension client is actually connected;
    * otherwise it launches Loom's own browser visibly;
    * explicit ``extension`` is strict and fails immediately when the extension
      is not connected instead of silently opening a different browser.

    The selected connection is resolved when ``browser_open`` starts a session,
    so enabling the extension after Loom has started takes effect on the next
    session without restarting the desktop app.
    """

    _browser_requested_connection: str = "local-launch"

    def _rewrite_auto_browser_open_description(self) -> None:
        tool = self.tools.get("browser_open")
        if tool is None:
            return
        description = (
            "Open a browser session using Loom's automatic connection policy. Prefer the user's currently active "
            "Chrome/Edge tab when the Loom Current Tab Bridge extension is connected; otherwise launch Loom's own "
            "visible isolated browser. The returned browser_connection always states which route was actually used. "
            "The extension route keeps the user's existing browser profile, cookies, login state, tabs, and page-local "
            "Browser HUD. The isolated fallback does not claim to be the user's current browser. allowed_domains can "
            "restrict the session."
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
        requested = str(mode or "").strip().casefold()
        if requested != _AUTO_MODE:
            # Let the concrete runtime validate and install the new route first.
            # If it rejects the change, status must continue describing the old
            # working connection instead of remembering a mode that never landed.
            super().browser_set_connection(
                requested,
                cdp_url=cdp_url,
                persist_profile=persist_profile,
                engine=engine,
            )
            self._browser_requested_connection = requested
            return self.browser_status()

        # Auto always keeps its fallback visible. Headless automation is a poor
        # fallback for a desktop product because the user cannot see what Loom is
        # doing, cannot complete CAPTCHA/MFA, and may mistake an isolated profile
        # for their signed-in browser.
        self.browser_headless = False

        # Use BrowserRuntime's ordinary local launch as the durable fallback. It
        # also validates active-session transitions and applies engine/profile
        # preferences. The extension bridge is then kept alive alongside it so a
        # browser extension that reconnects later is immediately usable.
        super().browser_set_connection(
            "local-launch",
            cdp_url="",
            persist_profile=persist_profile,
            engine=engine,
        )
        self._browser_requested_connection = _AUTO_MODE
        self._extension_bridge_for_session()
        self._rewrite_auto_browser_open_description()
        return self.browser_status()

    def browser_session_connection(
        self,
        connect: str,
        *,
        cdp_url: str = "",
    ):
        normalized = str(connect or "").strip().casefold().replace("-", "_")
        requested = str(getattr(self, "_browser_requested_connection", "") or "").strip().casefold()

        if normalized in {"", "default"} and requested in {_AUTO_MODE, "extension"}:
            bridge = self._extension_bridge_for_session()
            if bridge.connected:
                def build_extension(options):
                    return BrowserExtensionSessionBackend(options=options, bridge=bridge)

                return build_extension, True, "extension"
            if requested == "extension":
                raise RuntimeError(
                    "the Loom Current Tab Bridge extension is not connected. Install/enable "
                    "extensions/browser-current-tab in Chrome or Edge and make sure its bridge URL/token match Loom."
                )
            # Auto deliberately and visibly falls through to the Loom-owned
            # backend. The browser_open result labels this session local-launch.

        return super().browser_session_connection(connect, cdp_url=cdp_url)

    def browser_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        status = dict(super().browser_status(owner_session_id))
        requested = str(getattr(self, "_browser_requested_connection", "") or "").strip().casefold()
        status["requested_browser_connection"] = requested or status.get("browser_connection", "")
        status["headless"] = bool(getattr(self, "browser_headless", False))

        if requested == _AUTO_MODE:
            bridge = getattr(self, "browser_extension_bridge", None)
            connected = bool(bridge is not None and bridge.connected)
            selected = "extension" if connected else "local-launch"
            status["auto_selected_connection"] = selected
            status["browser_connection"] = "extension-bridge" if connected else "local-launch"
            status["external_browser"] = connected
            status["auto_fallback"] = not connected
            status["auto_fallback_reason"] = (
                ""
                if connected
                else "Current-tab extension is not connected; Loom will use its visible isolated browser for the next session."
            )
            if connected:
                # Do not leak the local fallback's derived profile facts into a
                # status that says the current browser is the selected route.
                status["backend"] = "browser-extension"
                status["session_persistence"] = "extension-current-browser"
                status["crash_recovery"] = "extension_reconnects_to_local_bridge"
                status["storage_state_persistence"] = True
                status["profile_name"] = "current-browser-extension"
            else:
                status["backend"] = "browser-use"
        return status


__all__ = ["BrowserAutoPolicyMixin"]
