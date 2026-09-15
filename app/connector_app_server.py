from __future__ import annotations

"""App-server bridge for product-level external connectors.

The connector runtime deliberately lives outside the generic MCP transport. MCP
continues to own protocol/tool-server execution; this module owns account
connection, credential lifecycle, health, and the product-facing RPC surface.

This patch module intentionally does *not* import ``app.connectors`` at module
import time. ``app.__init__`` installs Loom's runtime/MCP import patches before
the app-server is constructed; eagerly importing connectors here would import
``app.agent_runtime`` too early and bypass that patch chain.
"""

import copy
import importlib.abc
import importlib.machinery
import sys
import webbrowser
from types import ModuleType
from typing import Any

from app.import_patch_chain import find_spec_without


_TARGET_MODULE = "app.app_server_project_move"
_INSTALLED = False


def _connector_manager(runtime_home: Any):
    from app.connector_web_oauth import WebOAuthConnectorManager

    return WebOAuthConnectorManager(runtime_home)


def _mutating_action(action: str) -> bool:
    return action in {
        "connect_token",
        "import_gh",
        "start_auth",
        "poll_auth",
        "disconnect",
        "enable",
        "refresh",
        "configure_web_oauth",
        "clear_web_oauth",
    }


def patch(module: Any) -> None:
    service_cls = module.ProjectMovableLoomAppServerService
    controller_cls = module.ProjectMovableLoomRpcController
    if getattr(service_cls, "_loom_connectors_installed", False):
        return

    JsonRpcError = module.JsonRpcError
    original_service_init = service_cls.__init__
    original_runtime_status = service_cls.runtime_status
    original_initialize = controller_cls._initialize
    original_dispatch = controller_cls._dispatch

    def service_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_service_init(self, *args, **kwargs)
        runtime_home = self.store.root.parents[1]
        self.connectors = _connector_manager(runtime_home)
        self.connectors.install_runtime(self.runtime)
        from app.connector_step_provenance import install_connector_step_provenance

        install_connector_step_provenance(self.connectors, self.runtime)

    def runtime_status(self: Any) -> dict[str, Any]:
        status = dict(original_runtime_status(self))
        status["connectors"] = self.connectors.list()
        status["connectorStatus"] = {
            item["id"]: item for item in status["connectors"] if isinstance(item, dict) and item.get("id")
        }
        return status

    def _connector_changed(self: Any) -> dict[str, Any]:
        self.connectors.refresh_bound_runtime_tools()
        status = self.runtime_status()
        self._notify(
            "connector/updated",
            {
                "connectors": copy.deepcopy(status.get("connectors") or []),
                "runtime": copy.deepcopy(status),
            },
        )
        self._notify("runtime/updated", {"runtime": copy.deepcopy(status)})
        return status

    def connector_list(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        changed = self.connectors.refresh_if_store_changed()
        if changed:
            self.connectors.refresh_bound_runtime_tools()
        return {"connectors": self.connectors.list()}

    def connector_manage(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        from app.connectors import ConnectorError

        provider = str(params.get("provider") or params.get("id") or "github").strip().casefold()
        if provider != "github":
            raise JsonRpcError(-32602, f"unsupported connector provider: {provider}")
        action = self._required_text(params, "action").casefold()
        if _mutating_action(action):
            with self._guard:
                if self._active_sessions:
                    raise RuntimeError("finish active turns before changing connector authorization")

        try:
            if action == "status":
                result: dict[str, Any] = self.connectors.github_status()
                return {"connector": result}
            if action == "refresh":
                result = self.connectors.refresh()
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "enable":
                result = self.connectors.enable_github()
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "connect_token":
                token = str(params.get("token") or "").strip()
                if not token:
                    raise ValueError("token must not be empty")
                result = self.connectors.connect_token(token)
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "import_gh":
                result = self.connectors.import_github_cli()
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "configure_web_oauth":
                client_id = self._required_text(params, "clientId")
                client_secret = self._required_text(params, "clientSecret")
                result = self.connectors.configure_local_web_oauth(client_id, client_secret)
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "clear_web_oauth":
                result = self.connectors.clear_local_web_oauth()
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "disconnect":
                result = self.connectors.disconnect_github()
                status = _connector_changed(self)
                return {"connector": result, "runtime": status}
            if action == "start_auth":
                authorization = self.connectors.start_github_auth()
                target = str(
                    authorization.get("authorizationUrl")
                    or authorization.get("verificationUrl")
                    or ""
                ).strip()
                if target:
                    try:
                        webbrowser.open(target, new=2)
                    except Exception:
                        # The caller still receives the target URL and can show
                        # a retry/open action if the OS browser handoff fails.
                        pass
                return {"authorization": authorization}
            if action == "poll_auth":
                session_id = self._required_text(params, "sessionId")
                authorization = self.connectors.poll_github_auth(session_id)
                response: dict[str, Any] = {"authorization": authorization}
                if authorization.get("status") == "connected":
                    response["runtime"] = _connector_changed(self)
                return response
        except (ConnectorError, ValueError) as exc:
            raise JsonRpcError(-32060, str(exc)) from exc

        raise JsonRpcError(-32602, f"unsupported connector action: {action}")

    def initialize(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_initialize(self, params)
        capabilities = dict(result.get("capabilities") or {})
        capabilities["connectors"] = {
            "list": True,
            "manage": True,
            "providers": ["github"],
            "github": {
                "browserLogin": True,
                "loopbackOAuth": True,
                "pkce": True,
                "localOAuthConfiguration": True,
                "githubCliImport": True,
                "tokenImport": True,
                "disconnect": True,
            },
        }
        notifications = list(capabilities.get("notifications") or [])
        if "connector/updated" not in notifications:
            notifications.append("connector/updated")
        capabilities["notifications"] = notifications
        result["capabilities"] = capabilities
        # The parent initializer sampled runtime before this connector patch's
        # final state may have been included. Return the authoritative snapshot.
        result["runtime"] = self.service.runtime_status()
        return result

    def dispatch(self: Any, method: str, params: dict[str, Any]) -> Any:
        if method == "connector/list":
            return self.service.connector_list(params)
        if method == "connector/manage":
            return self.service.connector_manage(params)
        return original_dispatch(self, method, params)

    service_cls.__init__ = service_init
    service_cls.runtime_status = runtime_status
    service_cls.connector_list = connector_list
    service_cls.connector_manage = connector_manage
    service_cls._loom_connectors_installed = True
    controller_cls._initialize = initialize
    controller_cls._dispatch = dispatch


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _ConnectorLoader(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader) -> None:
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.loader, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        execute = getattr(self.loader, "exec_module", None)
        if not callable(execute):
            raise ImportError(f"loader for {_TARGET_MODULE} cannot execute modules")
        execute(module)
        patch(module)


class _ConnectorFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ConnectorLoader):
            return spec
        spec.loader = _ConnectorLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _ConnectorFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
