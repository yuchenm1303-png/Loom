from __future__ import annotations

"""Cross-agent GitHub connector credential synchronization.

Loom intentionally freezes connector authority inside a sampled model Step. That
must not mean a long-lived app-server process stays disconnected forever after
another Loom process (or ``gh auth login``) makes a shared credential available.

This patch keeps the Step authority boundary intact while adding two product
behaviors:

* a disconnected manager periodically re-probes shared credential sources at
  future Step boundaries even when ``connectors.json`` itself did not change;
* ``github_connection_status`` reports live connector state instead of the
  snapshot captured when that AgentTool object was created. If the explicit
  status check discovers a credential, it refreshes the long-lived registry for
  the *next* Step only; already sampled GitHub data/write handlers remain bound
  to their original authority.
"""

import json
from dataclasses import replace
from typing import Any


_REPROBE_SECONDS = 5.0
_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Import lazily. app.__init__ calls this installer only after release-time
    # connector environment metadata has been installed.
    from app.agent_runtime import ToolResult
    from app.connector_oauth_refresh import RefreshingConnectorManager

    manager_cls = RefreshingConnectorManager
    if getattr(manager_cls, "_loom_cross_agent_sync_installed", False):
        _INSTALLED = True
        return

    original_refresh_if_store_changed = manager_cls.refresh_if_store_changed
    original_agent_tools = manager_cls.agent_tools

    def refresh_if_store_changed(self: Any) -> bool:
        before = str(self.github_status().get("bindingId") or "")
        changed = bool(original_refresh_if_store_changed(self))
        if changed:
            return True

        status = self.github_status()
        if bool(status.get("connected")) or not bool(status.get("enabled", True)):
            return False

        now = float(self.clock())
        last = float(getattr(self, "_loom_last_external_credential_probe", float("-inf")))
        if now - last < _REPROBE_SECONDS:
            return False
        self._loom_last_external_credential_probe = now

        # Shared keyring / GH_TOKEN / GITHUB_TOKEN / gh CLI state can change
        # without touching connectors.json. Re-run normal validation, then tell
        # the runtime to rebuild future Step tools only if authority changed.
        self.refresh()
        after = str(self.github_status().get("bindingId") or "")
        return after != before

    def agent_tools(self: Any):
        tools = list(original_agent_tools(self))

        def live_status_handler(_context: Any, _arguments: dict[str, Any]) -> ToolResult:
            before = str(self.github_status().get("bindingId") or "")
            status = self.github_status()
            if not bool(status.get("connected")) and bool(status.get("enabled", True)):
                # An explicit status request is the user's request for fresh
                # health, so do not wait for the periodic Step-boundary probe.
                self._loom_last_external_credential_probe = float(self.clock())
                self.refresh()
            after = str(self.github_status().get("bindingId") or "")
            if after != before:
                # This only updates the long-lived registry. The ToolRouter for
                # the current Step already copied its GitHub handlers, so its
                # authority remains immutable.
                self.refresh_bound_runtime_tools()
            snapshot = dict(self.github_status())
            snapshot["usableFromNextStep"] = bool(snapshot.get("connected"))
            return ToolResult(
                ok=True,
                content=json.dumps(snapshot, ensure_ascii=False, indent=2),
                data={"connector": snapshot},
            )

        for index, tool in enumerate(tools):
            if tool.name == "github_connection_status":
                tools[index] = replace(tool, handler=live_status_handler)
                break
        return tuple(tools)

    manager_cls.refresh_if_store_changed = refresh_if_store_changed
    manager_cls.agent_tools = agent_tools
    manager_cls._loom_cross_agent_sync_installed = True
    _INSTALLED = True


__all__ = ["install"]
