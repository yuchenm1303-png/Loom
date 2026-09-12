"""Carrying a sign-in across browser sessions.

Only local-launch kept anything before this, and only through its profile
directory, so a cdp-attach or ephemeral session started signed out every time.
"""

from __future__ import annotations

import json

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserPageState
from app.agent_runtime.browser_tools import _SESSION_STATE_PATH
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("the model is never called in these tests")


SAVED_STATE = {
    "cookies": [
        {"name": "session", "value": "token-abc", "domain": "x.test", "path": "/"},
        {"name": "pref", "value": "dark", "domain": "x.test", "path": "/"},
    ],
    "origins": [
        {"origin": "https://x.test", "localStorage": [{"name": "k", "value": "v"}]},
        {"origin": "https://empty.test", "localStorage": []},
    ],
}


@pytest.fixture
def session(tmp_path):
    runtime = BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    restored: list[list[dict]] = []

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None

        def start(self):
            return BrowserPageState(url="https://x.test/", title="X")

        def state(self):
            return BrowserPageState(url="https://x.test/", title="X")

        def storage_state(self):
            return json.loads(json.dumps(SAVED_STATE))

        def set_cookies(self, cookies):
            restored.append([dict(item) for item in cookies])

        def close(self):
            return None

    runtime.browser_sessions.backend_factory = lambda options: _Backend()
    context = ToolContext(session_id="s-1", turn_id="t-1", workspace=workspace)
    browser_id = runtime.tools.get("browser_open").handler(context, {}).data["browser_id"]
    yield runtime, context, browser_id, workspace, restored
    runtime.close()


def _call(runtime, context, browser_id, **kwargs):
    return runtime.tools.get("browser_session_state").handler(
        context, {"browser_id": browser_id, **kwargs}
    )


def test_save_writes_the_state_into_the_workspace(session):
    runtime, context, browser_id, workspace, _ = session

    result = _call(runtime, context, browser_id, action="save")

    target = workspace / _SESSION_STATE_PATH
    assert target.is_file()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == SAVED_STATE
    assert result.data["cookies"] == 2
    # Only origins that actually hold something are reported.
    assert result.data["storage_origins"] == ["https://x.test"]


def test_save_says_out_loud_that_the_file_holds_credentials(session):
    runtime, context, browser_id, _, _ = session

    result = _call(runtime, context, browser_id, action="save")

    assert "plain text" in result.content
    assert "credentials" in result.content


def test_load_restores_the_cookies_verbatim(session):
    runtime, context, browser_id, _, restored = session
    _call(runtime, context, browser_id, action="save")

    result = _call(runtime, context, browser_id, action="load")

    assert restored == [SAVED_STATE["cookies"]]
    assert result.data["cookies_restored"] == 2


def test_load_names_the_storage_it_did_not_restore(session):
    """Writing web storage needs to be on its origin, so the model has to know."""

    runtime, context, browser_id, _, _ = session
    _call(runtime, context, browser_id, action="save")

    result = _call(runtime, context, browser_id, action="load")

    assert result.data["storage_origins_not_restored"] == ["https://x.test"]
    assert "browser_eval" in result.content


def test_a_custom_path_is_honoured_and_confined_to_the_workspace(session):
    runtime, context, browser_id, workspace, _ = session

    _call(runtime, context, browser_id, action="save", path="logins/x.json")
    assert (workspace / "logins" / "x.json").is_file()

    with pytest.raises(ValueError, match="escapes the agent workspace"):
        _call(runtime, context, browser_id, action="save", path="../../escaped.json")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"action": "delete"}, "must be save or load"),
        ({"action": "load", "path": "missing/none.json"}, "no saved browser session state"),
    ],
)
def test_bad_requests_are_refused(session, kwargs, expected):
    runtime, context, browser_id, _, _ = session
    with pytest.raises(ValueError, match=expected):
        _call(runtime, context, browser_id, **kwargs)


def test_a_corrupt_saved_file_is_reported_rather_than_restored(session):
    runtime, context, browser_id, workspace, restored = session
    target = workspace / _SESSION_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable"):
        _call(runtime, context, browser_id, action="load")
    assert restored == []


def test_a_saved_file_that_is_not_an_object_is_refused(session):
    runtime, context, browser_id, workspace, restored = session
    target = workspace / _SESSION_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ValueError, match="not an object"):
        _call(runtime, context, browser_id, action="load")
    assert restored == []


def test_non_object_cookie_rows_are_dropped_instead_of_reaching_the_browser(session):
    runtime, context, browser_id, workspace, restored = session
    target = workspace / _SESSION_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"cookies": [{"name": "ok", "value": "1"}, "junk", 7, None]}),
        encoding="utf-8",
    )

    _call(runtime, context, browser_id, action="load")

    assert restored == [[{"name": "ok", "value": "1"}]]
