from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent_runtime import ToolEffect, ToolExposure
from app.connectors import ConnectorManager


class FakeVault:
    def __init__(self, token: str = "") -> None:
        self.values: dict[str, str] = {}
        if token:
            self.values["github/access-token"] = token

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


@dataclass
class FakeCommandResult:
    returncode: int = 1
    stdout: str = ""
    stderr: str = ""


class FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def user(self):
        if self.token == "bad":
            raise RuntimeError("invalid credential")
        return {"login": "alice"}, {"X-OAuth-Scopes": "repo, read:org"}

    def request(self, method: str, path: str, *, query=None, body=None):
        if self.token == "bad":
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
                "permissions": {"pull": True, "push": True},
                "updated_at": "2026-09-15T00:00:00Z",
            }, {}
        if method == "GET" and path == "/user/repos":
            return [
                {
                    "id": 42,
                    "name": "widgets",
                    "full_name": "acme/widgets",
                    "private": True,
                    "archived": False,
                    "default_branch": "main",
                    "owner": {"login": "acme"},
                }
            ], {}
        if method == "POST" and path == "/repos/acme/widgets/issues":
            return {
                "number": 9,
                "title": body["title"],
                "state": "open",
                "html_url": "https://github.com/acme/widgets/issues/9",
                "user": {"login": "alice"},
                "body": body.get("body", ""),
            }, {}
        raise AssertionError(f"unexpected fake GitHub request: {method} {path} {query} {body}")


def _manager(tmp_path: Path, *, token: str = "", environment=None, vault=None) -> ConnectorManager:
    return ConnectorManager(
        tmp_path / "home",
        vault=vault or FakeVault(token),
        environment={} if environment is None else environment,
        client_factory=FakeGitHubClient,
        command_runner=lambda *args, **kwargs: FakeCommandResult(),
    )


def _tool(manager: ConnectorManager, name: str):
    return next(tool for tool in manager.agent_tools() if tool.name == name)


def test_connector_status_is_secret_free(tmp_path: Path) -> None:
    secret = "github-secret-that-must-never-leak"
    manager = _manager(tmp_path, token=secret)

    status = manager.github_status()
    assert status["connected"] is True
    assert status["account"] == "alice"
    assert status["credentialSource"] == "keyring"
    assert secret not in repr(status)
    assert secret not in status["bindingId"]

    status_tool = _tool(manager, "github_connection_status")
    result = status_tool.handler(None, {})
    assert result.ok is True
    assert secret not in result.content
    assert secret not in repr(result.data)


def test_device_flow_validates_and_vaults_token_without_returning_it(tmp_path: Path) -> None:
    vault = FakeVault()
    manager = _manager(
        tmp_path,
        environment={"LOOM_GITHUB_CLIENT_ID": "loom-client-id"},
        vault=vault,
    )
    responses = [
        {
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        },
        {
            "access_token": "oauth-access-secret",
            "token_type": "bearer",
            "scope": "repo read:org",
        },
    ]
    calls: list[tuple[str, dict[str, object]]] = []

    def oauth_post(url, payload, *, timeout_seconds=20.0):
        _ = timeout_seconds
        calls.append((url, dict(payload)))
        return responses.pop(0)

    manager._oauth_post = oauth_post  # type: ignore[method-assign]

    started = manager.start_github_auth()
    assert started["mode"] == "device"
    assert started["status"] == "pending"
    assert started["userCode"] == "ABCD-EFGH"
    assert "device-secret" not in repr(started)

    completed = manager.poll_github_auth(started["sessionId"])
    assert completed["status"] == "connected"
    assert completed["connector"]["account"] == "alice"
    assert "oauth-access-secret" not in repr(completed)
    assert vault.values["github/access-token"] == "oauth-access-secret"
    assert calls[0][1]["client_id"] == "loom-client-id"
    assert calls[1][1]["device_code"] == "device-secret"


def test_connector_tool_binding_freezes_authority_per_tool_snapshot(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    old_tool = _tool(manager, "github_repository_get")
    assert old_tool.binding_key == "connector:github:disconnected"

    connected = manager.connect_token("token-one")
    assert connected["connected"] is True
    new_tool = _tool(manager, "github_repository_get")
    assert new_tool.binding_key != old_tool.binding_key

    # A previously captured AgentTool must not start using credentials that did
    # not exist when that model Step was sampled.
    old_result = old_tool.handler(None, {"repository": "acme/widgets"})
    assert old_result.ok is False
    assert "not connected" in old_result.content

    new_result = new_tool.handler(None, {"repository": "acme/widgets"})
    assert new_result.ok is True
    assert new_result.data["repository"]["full_name"] == "acme/widgets"

    # Switching credentials creates a third binding; it never retargets either
    # existing handler closure.
    manager.connect_token("token-two")
    newest_tool = _tool(manager, "github_repository_get")
    assert newest_tool.binding_key not in {old_tool.binding_key, new_tool.binding_key}
    assert new_tool.handler(None, {"repository": "acme/widgets"}).ok is True


def test_disconnect_is_sticky_even_when_environment_has_token(tmp_path: Path) -> None:
    manager = _manager(tmp_path, environment={"GH_TOKEN": "ambient-token"})
    assert manager.github_status()["connected"] is True
    assert manager.github_status()["credentialSource"] == "GH_TOKEN"

    disconnected = manager.disconnect_github()
    assert disconnected["connected"] is False
    assert disconnected["enabled"] is False

    # Refresh must not silently reconnect from GH_TOKEN after an explicit user
    # disconnect. Re-enabling is an explicit product action.
    refreshed = manager.refresh()
    assert refreshed["connected"] is False
    assert refreshed["enabled"] is False

    enabled = manager.enable_github()
    assert enabled["connected"] is True
    assert enabled["credentialSource"] == "GH_TOKEN"


def test_github_write_tools_cross_sensitive_approval_boundary(tmp_path: Path) -> None:
    manager = _manager(tmp_path, token="token-one")
    tools = {tool.name: tool for tool in manager.agent_tools()}

    read_names = {
        "github_repository_get",
        "github_repository_list",
        "github_file_read",
        "github_code_search",
        "github_issue_list",
        "github_pull_request_list",
        "github_pull_request_get",
        "github_workflow_runs_list",
    }
    write_names = {
        "github_issue_create",
        "github_issue_comment",
        "github_pull_request_create",
        "github_branch_create",
        "github_file_write",
    }

    for name in read_names:
        assert tools[name].effect is ToolEffect.READ_ONLY
        assert tools[name].exposure is ToolExposure.DEFERRED
    for name in write_names:
        assert tools[name].effect is ToolEffect.SENSITIVE
        assert tools[name].exposure is ToolExposure.DEFERRED

    created = tools["github_issue_create"].handler(
        None,
        {"repository": "acme/widgets", "title": "Connector fixture", "body": "safe fixture"},
    )
    assert created.ok is True
    assert created.data["issue"]["number"] == 9


# --- launch-time credential check ------------------------------------------------
#
# Constructing a manager used to run the check inline: it spawns `gh` and calls
# api.github.com, so on a poor network the app-server could not answer
# `initialize` until those timeouts had run out.


class _GatedClient:
    """A GitHub client whose `user()` waits for the test to let it finish."""

    started = None
    release = None

    def __init__(self, token: str) -> None:
        self.token = token

    def user(self):
        type(self).started.set()
        assert type(self).release.wait(5), "the test never released the GitHub call"
        return {"login": "alice"}, {"X-OAuth-Scopes": "repo"}


def _gated_manager(tmp_path: Path, **kwargs):
    import threading

    client = type("GatedClient", (_GatedClient,), {"started": threading.Event(), "release": threading.Event()})
    manager = ConnectorManager(
        tmp_path / "home",
        vault=FakeVault("token-one"),
        environment={},
        client_factory=client,
        command_runner=lambda *args, **kwargs: FakeCommandResult(),
        defer_initial_refresh=True,
        **kwargs,
    )
    return manager, client


def test_deferred_manager_does_not_touch_the_network_while_being_constructed(tmp_path: Path) -> None:
    manager, client = _gated_manager(tmp_path)

    assert not client.started.is_set()
    status = manager.github_status()
    assert status["connected"] is False
    assert status["checking"] is True


def test_status_reads_do_not_wait_for_a_slow_credential_check(tmp_path: Path) -> None:
    import threading

    manager, client = _gated_manager(tmp_path)
    manager.start_initial_refresh()
    assert client.started.wait(2)

    answered = threading.Event()
    threading.Thread(target=lambda: (manager.github_status(), answered.set()), daemon=True).start()
    try:
        # The check is still blocked in the network call. The app-server answers
        # `initialize` from this status, so it must not queue behind that call.
        assert answered.wait(1), "github_status() waited for the credential check"
    finally:
        client.release.set()
    assert manager.wait_for_initial_refresh(2)
    assert manager.github_status()["connected"] is True
    assert manager.github_status()["checking"] is False


def test_finished_initial_check_reports_back_and_rebinds_the_tools(tmp_path: Path) -> None:
    import threading

    manager, client = _gated_manager(tmp_path)
    rebinds = []
    manager.refresh_bound_runtime_tools = lambda: rebinds.append("rebind")
    finished = threading.Event()
    manager.start_initial_refresh(finished.set)
    client.release.set()

    assert finished.wait(2)
    assert rebinds == ["rebind"]


def test_a_check_that_started_before_a_disconnect_cannot_reconnect_afterwards(tmp_path: Path) -> None:
    manager, client = _gated_manager(tmp_path)
    manager.start_initial_refresh()
    assert client.started.wait(2)

    # The user disconnects while the launch-time check is still in flight.
    manager.disconnect_github()
    client.release.set()
    assert manager.wait_for_initial_refresh(2)

    status = manager.github_status()
    assert status["connected"] is False
    assert status["enabled"] is False


def test_a_step_waits_for_the_initial_check_only_when_one_is_running(tmp_path: Path) -> None:
    manager, client = _gated_manager(tmp_path)

    # Deferred but never started: waiting would only burn the timeout on every step.
    assert manager.wait_for_initial_refresh(5) is False

    manager.start_initial_refresh()
    assert client.started.wait(2)
    assert manager.wait_for_initial_refresh(0.05) is False
    client.release.set()
    assert manager.wait_for_initial_refresh(2) is True


def test_the_github_cli_is_not_run_when_an_earlier_credential_already_works(tmp_path: Path) -> None:
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return FakeCommandResult(returncode=0, stdout="cli-token")

    manager = ConnectorManager(
        tmp_path / "home",
        vault=FakeVault("keyring-token"),
        environment={},
        client_factory=FakeGitHubClient,
        command_runner=runner,
    )
    # Pretend `gh` is installed, so that running it would have been possible.
    manager._gh_binary = lambda: "gh"
    manager.refresh()

    assert manager.github_status()["credentialSource"] == "keyring"
    assert calls == [], "gh was spawned even though the keyring token validated"
