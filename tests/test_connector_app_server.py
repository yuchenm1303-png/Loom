from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import connector_app_server


class FakeRuntime:
    def __init__(self) -> None:
        self.tools = SimpleNamespace()


class FakeManager:
    instances: list["FakeManager"] = []

    def __init__(self, runtime_home: Path) -> None:
        self.runtime_home = Path(runtime_home)
        self.runtime = None
        self.connected = False
        self.calls: list[tuple[str, object]] = []
        FakeManager.instances.append(self)

    def install_runtime(self, runtime) -> None:
        self.runtime = runtime

    def refresh_bound_runtime_tools(self) -> None:
        self.calls.append(("refresh-tools", None))

    def refresh_if_store_changed(self) -> bool:
        return False

    def list(self):
        return [self.github_status()]

    def github_status(self):
        return {
            "id": "github",
            "name": "GitHub",
            "connected": self.connected,
            "enabled": True,
            "account": "alice" if self.connected else "",
            "credentialSource": "keyring" if self.connected else "",
            "bindingId": "github:test" if self.connected else "github:disconnected",
            "error": "" if self.connected else "No GitHub credential found",
        }

    def connect_token(self, token: str):
        self.calls.append(("connect_token", token))
        self.connected = True
        return self.github_status()

    def import_github_cli(self):
        self.calls.append(("import-gh", None))
        self.connected = True
        return self.github_status()

    def disconnect_github(self):
        self.calls.append(("disconnect", None))
        self.connected = False
        return self.github_status()

    def enable_github(self):
        self.calls.append(("enable", None))
        return self.github_status()

    def refresh(self):
        self.calls.append(("refresh", None))
        return self.github_status()

    def start_github_auth(self):
        self.calls.append(("start-auth", None))
        return {
            "sessionId": "auth-1",
            "status": "pending",
            "mode": "device",
            "verificationUrl": "https://github.com/login/device",
        }

    def poll_github_auth(self, session_id: str):
        self.calls.append(("poll-auth", session_id))
        self.connected = True
        return {"sessionId": session_id, "status": "connected", "connector": self.github_status()}


class BaseService:
    def __init__(self, *args, **kwargs) -> None:
        _ = args
        root = Path(kwargs.pop("root", Path.cwd() / ".loom"))
        sessions = root / "agent_runtime" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        self.store = SimpleNamespace(root=sessions)
        self.runtime = FakeRuntime()
        self._guard = threading.RLock()
        self._active_sessions: set[str] = set()
        self.notifications: list[tuple[str, dict]] = []

    def runtime_status(self):
        return {"base": True}

    def _notify(self, method: str, params: dict) -> None:
        self.notifications.append((method, params))

    @staticmethod
    def _required_text(params: dict, key: str) -> str:
        value = str(params.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} must not be empty")
        return value


class BaseController:
    def __init__(self, service) -> None:
        self.service = service

    def _initialize(self, params):
        _ = params
        return {"capabilities": {"notifications": ["runtime/updated"]}, "runtime": {"stale": True}}

    def _dispatch(self, method, params):
        return {"fallback": method, "params": params}


class FakeJsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_real_app_server_import_chain_receives_connector_patch() -> None:
    from app.app_server_project_move import (
        ProjectMovableLoomAppServerService,
        ProjectMovableLoomRpcController,
    )

    assert getattr(ProjectMovableLoomAppServerService, "_loom_connectors_installed", False) is True
    assert callable(getattr(ProjectMovableLoomAppServerService, "connector_list", None))
    assert callable(getattr(ProjectMovableLoomAppServerService, "connector_manage", None))
    assert ProjectMovableLoomRpcController._dispatch is not BaseController._dispatch


def _patched(monkeypatch):
    FakeManager.instances.clear()
    monkeypatch.setattr(connector_app_server, "_connector_manager", lambda runtime_home: FakeManager(runtime_home))
    monkeypatch.setattr(connector_app_server.webbrowser, "open", lambda *args, **kwargs: True)
    service_cls = type("ProjectMovableLoomAppServerService", (BaseService,), {})
    controller_cls = type("ProjectMovableLoomRpcController", (BaseController,), {})
    module = SimpleNamespace(
        ProjectMovableLoomAppServerService=service_cls,
        ProjectMovableLoomRpcController=controller_cls,
        JsonRpcError=FakeJsonRpcError,
    )
    connector_app_server.patch(module)
    return service_cls, controller_cls


def test_connector_rpc_is_advertised_and_runtime_reports_health(monkeypatch, tmp_path: Path) -> None:
    service_cls, controller_cls = _patched(monkeypatch)
    service = service_cls(root=tmp_path / "home")
    controller = controller_cls(service)

    initialized = controller._initialize({})
    assert initialized["capabilities"]["connectors"]["providers"] == ["github"]
    assert "connector/updated" in initialized["capabilities"]["notifications"]
    assert initialized["runtime"]["connectorStatus"]["github"]["connected"] is False

    listed = controller._dispatch("connector/list", {})
    assert listed["connectors"][0]["id"] == "github"
    assert FakeManager.instances[0].runtime is service.runtime


def test_connector_authority_cannot_change_during_active_turn(monkeypatch, tmp_path: Path) -> None:
    service_cls, _controller_cls = _patched(monkeypatch)
    service = service_cls(root=tmp_path / "home")
    manager = FakeManager.instances[0]
    service._active_sessions.add("thread-running")

    with pytest.raises(RuntimeError, match="finish active turns"):
        service.connector_manage({"provider": "github", "action": "connect_token", "token": "secret"})
    assert manager.calls == []


def test_connector_token_is_transient_and_update_notifications_are_secret_free(monkeypatch, tmp_path: Path) -> None:
    service_cls, _controller_cls = _patched(monkeypatch)
    service = service_cls(root=tmp_path / "home")
    manager = FakeManager.instances[0]
    secret = "transient-secret-token"

    result = service.connector_manage(
        {"provider": "github", "action": "connect_token", "token": secret}
    )
    assert result["connector"]["connected"] is True
    assert manager.calls[0] == ("connect_token", secret)
    assert secret not in repr(result)
    assert secret not in repr(service.notifications)
    assert any(method == "connector/updated" for method, _ in service.notifications)
    assert any(method == "runtime/updated" for method, _ in service.notifications)


def test_browser_auth_poll_refreshes_runtime_only_after_connection(monkeypatch, tmp_path: Path) -> None:
    service_cls, _controller_cls = _patched(monkeypatch)
    service = service_cls(root=tmp_path / "home")

    started = service.connector_manage({"provider": "github", "action": "start_auth"})
    assert started["authorization"]["status"] == "pending"

    completed = service.connector_manage(
        {"provider": "github", "action": "poll_auth", "sessionId": "auth-1"}
    )
    assert completed["authorization"]["status"] == "connected"
    assert completed["runtime"]["connectorStatus"]["github"]["connected"] is True
