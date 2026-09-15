from __future__ import annotations

import subprocess
from pathlib import Path

from app.connector_oauth_refresh import RefreshingConnectorManager


class SharedVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def user(self):
        if self.token != "shared-token":
            raise RuntimeError("invalid credential")
        return {"login": "shared-user"}, {"X-OAuth-Scopes": "repo"}

    def request(self, method: str, path: str, *, query=None, body=None):
        _ = query, body
        if self.token != "shared-token":
            raise RuntimeError("invalid credential")
        if method == "GET" and path == "/repos/acme/widgets":
            return {
                "id": 42,
                "name": "widgets",
                "full_name": "acme/widgets",
                "private": True,
                "archived": False,
                "default_branch": "main",
                "description": "fixture",
                "html_url": "https://github.com/acme/widgets",
                "owner": {"login": "acme"},
                "permissions": {"pull": True},
                "updated_at": "2026-09-15T00:00:00Z",
            }, {}
        raise AssertionError(f"unexpected request: {method} {path}")


def _failed_command(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in")


def _tool(manager: RefreshingConnectorManager, name: str):
    return next(tool for tool in manager.agent_tools() if tool.name == name)


def test_disconnected_manager_reprobes_shared_vault_without_state_file_change(tmp_path: Path) -> None:
    vault = SharedVault()
    monotonic = [0.0]
    manager = RefreshingConnectorManager(
        tmp_path / "home",
        vault=vault,
        environment={},
        client_factory=FakeGitHubClient,
        command_runner=_failed_command,
        clock=lambda: monotonic[0],
        wall_clock=lambda: 1_000.0,
    )
    assert manager.github_status()["connected"] is False
    original_mtime = manager.state_store.mtime_ns()

    # Simulate another Loom process (or an external credential flow) updating
    # the shared OS credential store without touching connectors.json.
    vault.set("github/access-token", "shared-token")
    assert manager.state_store.mtime_ns() == original_mtime

    monotonic[0] = 6.0
    assert manager.refresh_if_store_changed() is True
    status = manager.github_status()
    assert status["connected"] is True
    assert status["account"] == "shared-user"
    assert status["credentialSource"] == "keyring"


def test_live_status_discovers_shared_credential_without_retargeting_old_step_tools(tmp_path: Path) -> None:
    vault = SharedVault()
    manager = RefreshingConnectorManager(
        tmp_path / "home",
        vault=vault,
        environment={},
        client_factory=FakeGitHubClient,
        command_runner=_failed_command,
        clock=lambda: 10.0,
        wall_clock=lambda: 1_000.0,
    )

    # These represent tools already copied into one sampled model Step.
    old_status = _tool(manager, "github_connection_status")
    old_repo = _tool(manager, "github_repository_get")
    assert old_repo.binding_key == "connector:github:disconnected"

    vault.set("github/access-token", "shared-token")

    status_result = old_status.handler(None, {})
    assert status_result.ok is True
    assert status_result.data["connector"]["connected"] is True
    assert status_result.data["connector"]["account"] == "shared-user"
    assert status_result.data["connector"]["usableFromNextStep"] is True

    # The current Step still cannot gain authority that it did not have when it
    # was sampled.
    old_result = old_repo.handler(None, {"repository": "acme/widgets"})
    assert old_result.ok is False
    assert "not connected" in old_result.content

    # A future Step gets a newly sampled tool bound to the shared credential.
    new_repo = _tool(manager, "github_repository_get")
    assert new_repo.binding_key != old_repo.binding_key
    new_result = new_repo.handler(None, {"repository": "acme/widgets"})
    assert new_result.ok is True
    assert new_result.data["repository"]["full_name"] == "acme/widgets"
