"""Script evaluation, cookies, storage and downloads.

These are the capabilities that reach past the DOM-index surface: running page
script, reading the signed-in session's cookies, and writing files into the
workspace. They are deliberately available, so what is pinned here is the shape
of what they hand back - bounded values, withheld cookie values, and download
paths confined to the workspace.
"""

from __future__ import annotations

import json

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_tools import (
    _DOWNLOADS_DIR,
    _bounded_json_value,
    _cookie_row,
)
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("the model is never called in these tests")


@pytest.fixture
def runtime(tmp_path):
    built = BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    yield built
    built.close()


def test_the_page_capabilities_are_registered_and_gated_like_other_actions(runtime):
    for name in ("browser_eval", "browser_cookies", "browser_storage", "browser_downloads"):
        tool = runtime.tools.get(name)
        assert tool is not None, f"{name} is not registered"
        # Each reaches into a live page, so none may be READ_ONLY: that effect
        # skips approval, and read-only permission mode closes browser sessions.
        assert tool.effect.value == "sensitive"


def test_eval_description_does_not_hide_what_it_can_reach(runtime):
    description = runtime.tools.get("browser_eval").description.casefold()
    assert "page's own privileges" in description
    assert "signed into" in description


def test_cookie_description_says_what_clearing_does(runtime):
    description = runtime.tools.get("browser_cookies").description.casefold()
    assert "signs the browser out" in description


class TestBoundedEvalValue:
    def test_small_values_pass_through_unchanged(self):
        assert _bounded_json_value({"a": [1, 2]}) == ({"a": [1, 2]}, False)
        assert _bounded_json_value(None) == (None, False)
        assert _bounded_json_value(0) == (0, False)

    def test_an_oversized_value_is_replaced_rather_than_trimmed_in_place(self):
        """Half a JSON structure would read to the model as real data."""

        value, truncated = _bounded_json_value(["x" * 40_000])

        assert truncated is True
        assert isinstance(value, str)
        assert len(value) == 30_000

    def test_a_value_json_cannot_encode_still_reports_something(self):
        value, truncated = _bounded_json_value({1, 2, 3})
        assert truncated is True
        assert isinstance(value, str)
        assert value


class TestCookieRows:
    def test_the_value_is_withheld_unless_asked_for(self):
        raw = {
            "name": "session",
            "value": "super-secret-session",
            "domain": "example.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "Lax",
            "expires": 123,
        }

        withheld = _cookie_row(raw, include_values=False)
        assert "value" not in withheld
        assert "super-secret-session" not in json.dumps(withheld)
        # The length still tells the model the cookie is set and roughly how big.
        assert withheld["value_chars"] == len("super-secret-session")
        assert withheld["http_only"] is True
        assert withheld["secure"] is True

        included = _cookie_row(raw, include_values=True)
        assert included["value"] == "super-secret-session"

    def test_a_long_value_is_capped(self):
        row = _cookie_row({"name": "big", "value": "v" * 9000}, include_values=True)
        assert len(row["value"]) == 4000
        assert row["value_chars"] == 9000

    def test_a_junk_entry_does_not_raise(self):
        assert _cookie_row(None, include_values=True)["name"] == ""


def test_downloads_land_in_the_workspace_directory_the_tool_advertises(runtime):
    tool = runtime.tools.get("browser_downloads")
    assert _DOWNLOADS_DIR in tool.description
    # The model reads downloads back with the ordinary file tools, so the
    # directory has to be a workspace-relative name, not an absolute path.
    assert not _DOWNLOADS_DIR.startswith("/")
    assert ":" not in _DOWNLOADS_DIR


def test_open_attaches_a_workspace_downloads_directory_to_the_backend(runtime, tmp_path):
    """A download that lands outside the workspace is unreadable by the model."""

    from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
    from app.agent_runtime.tools import ToolContext

    workspace = tmp_path / "ws"
    workspace.mkdir()
    built: list[object] = []

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None

        def start(self):
            built.append(self)
            return BrowserPageState(url="https://example.com/", title="Example")

        def state(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def close(self):
            return None

    runtime.browser_sessions.backend_factory = lambda options: _Backend()
    context = ToolContext(session_id="s-1", turn_id="t-1", workspace=workspace)

    runtime.tools.get("browser_open").handler(context, {})

    assert built, "the session never started"
    configured = str(getattr(built[0], "downloads_dir", "") or "")
    assert configured
    assert (workspace / _DOWNLOADS_DIR).is_dir()
    assert configured.replace("\\", "/").endswith(_DOWNLOADS_DIR)
    # Nothing may point downloads outside the workspace.
    assert str(workspace).replace("\\", "/") in configured.replace("\\", "/")


def test_eval_reports_a_page_exception_as_a_result_not_a_raised_tool_error(runtime, tmp_path):
    from app.agent_runtime.browser_session import BrowserPageState
    from app.agent_runtime.tools import ToolContext

    workspace = tmp_path / "ws2"
    workspace.mkdir()

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None

        def start(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def state(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def evaluate(self, expression, *, await_promise=True):
            return {"ok": False, "error": "Error: boom from page"}

        def close(self):
            return None

    runtime.browser_sessions.backend_factory = lambda options: _Backend()
    context = ToolContext(session_id="s-2", turn_id="t-2", workspace=workspace)
    opened = runtime.tools.get("browser_open").handler(context, {})
    browser_id = opened.data["browser_id"]

    result = runtime.tools.get("browser_eval").handler(
        context, {"browser_id": browser_id, "expression": "throw new Error('x')"}
    )

    assert result.ok is False
    assert "boom from page" in result.content
