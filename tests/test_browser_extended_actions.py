"""Extended browser actions, and the silent no-op that hid inside browser_select.

browser_select went through the actor Element's select_option, which returned
without error and without selecting anything - the page value never moved and no
change event fired, for option text and option value alike. Nothing caught it
because nothing asserted on the provider call it makes, so these pin the call
and the success check rather than only the happy path.
"""

from __future__ import annotations

import json

import pytest

from app.agent_runtime.browser_backend import (
    _dropdown_selection_succeeded,
    _normalize_dropdown_options,
)
from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
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


def test_the_new_actions_are_registered_and_sensitive(runtime):
    names = {tool.name: tool for tool in runtime.tools.all()}
    for name in ("browser_forward", "browser_find", "browser_dropdown_options", "browser_upload"):
        assert name in names, f"{name} is not registered"


def test_upload_and_drag_descriptions_state_what_they_do_not_guarantee(runtime):
    upload = runtime.tools.get("browser_upload").description
    # Uploading hands a local file to a remote site; that must be said out loud.
    assert "workspace" in upload
    assert "sends the file" in upload.casefold()

    drag = runtime.tools.get("browser_drag").description
    # Synthetic mouse events cannot start a native HTML5 drag, and such a page
    # reports no error, so the model has to be told to verify.
    assert "html5" in drag.casefold()


def test_find_reports_a_miss_as_an_answer_rather_than_a_failed_call():
    from app.agent_runtime.browser_session import BrowserError, BrowserTextNotFoundError

    # The tool layer catches this specific type. It must stay a BrowserError so
    # existing handling keeps working, and must not be the class browser-use
    # exports under the same name.
    assert issubclass(BrowserTextNotFoundError, BrowserError)

    from browser_use.browser.views import BrowserError as ProviderBrowserError

    assert not issubclass(ProviderBrowserError, BrowserError)
    assert not issubclass(BrowserTextNotFoundError, ProviderBrowserError)


class TestDropdownOptionNormalization:
    def test_reads_the_json_string_browser_use_actually_returns(self):
        raw = {
            "type": "select",
            "options": json.dumps(
                [
                    {"text": "Red", "value": "r", "index": 0, "selected": False},
                    {"text": "Green", "value": "g", "index": 1, "selected": True},
                ]
            ),
            "message": "Use the exact text or value string in select_dropdown(index=48, text=...)",
            "short_term_memory": "Found select dropdown ...",
        }

        options = _normalize_dropdown_options(raw)

        assert options == [
            {"text": "Red", "value": "r", "selected": False},
            {"text": "Green", "value": "g", "selected": True},
        ]
        # The provider's prose names its own tools and would mislead the model.
        assert "select_dropdown" not in json.dumps(options)

    def test_accepts_a_plain_list_too(self):
        options = _normalize_dropdown_options([{"text": "One", "value": "1"}])
        assert options == [{"text": "One", "value": "1", "selected": False}]

    def test_drops_entries_with_neither_text_nor_value(self):
        assert _normalize_dropdown_options([{"selected": True}, {"text": "Keep"}]) == [
            {"text": "Keep", "value": "", "selected": False}
        ]

    def test_caps_a_pathological_option_list(self):
        raw = [{"text": f"opt-{i}", "value": str(i)} for i in range(1000)]
        assert len(_normalize_dropdown_options(raw)) == 300

    def test_unparseable_payloads_degrade_to_empty(self):
        assert _normalize_dropdown_options({"options": "[not json"}) == []
        assert _normalize_dropdown_options(None) == []
        assert _normalize_dropdown_options("nope") == []


class TestDropdownSelectionVerdict:
    def test_the_string_false_is_not_treated_as_success(self):
        """success arrives as a string, so truth-testing it accepts "false"."""

        assert _dropdown_selection_succeeded({"success": "false"}) is False
        assert _dropdown_selection_succeeded({"success": "true"}) is True

    def test_real_success_payload_is_accepted(self):
        assert _dropdown_selection_succeeded(
            {"success": "true", "message": "Selected option: Blue (value: b)", "value": "b"}
        ) is True

    def test_a_reported_value_stands_in_for_a_missing_success_flag(self):
        assert _dropdown_selection_succeeded({"message": "Selected", "value": "b"}) is True
        assert _dropdown_selection_succeeded({"message": "Selected"}) is False

    def test_no_result_is_not_success(self):
        assert _dropdown_selection_succeeded(None) is False

    def test_booleans_pass_through(self):
        assert _dropdown_selection_succeeded(True) is True
        assert _dropdown_selection_succeeded(False) is False


def test_select_dispatches_the_dropdown_event_and_refuses_an_unmatched_option():
    """The regression that made browser_select do nothing at all.

    The actor Element path it used reported success without touching the page, so
    this asserts the dropdown event is the one dispatched and that a watchdog
    verdict of failure becomes an error instead of a silent success.
    """

    import asyncio
    from types import SimpleNamespace

    from app.agent_runtime.browser_backend import BrowserUseSessionBackend
    from app.agent_runtime.browser_session import BrowserError, BrowserLaunchOptions

    # SelectDropdownOptionEvent copies these off the node when it validates, so a
    # bare sentinel is rejected before the dispatch under test happens.
    def dom_node():
        return SimpleNamespace(
            node_id=7,
            backend_node_id=7,
            session_id="sess",
            frame_id="frame",
            target_id="target",
            node_type=1,
            node_name="SELECT",
            node_value="",
            attributes={},
            is_scrollable=False,
            is_visible=True,
            absolute_position=None,
        )

    dispatched: list[object] = []

    class _Dispatch:
        def __init__(self, outcome):
            self.outcome = outcome

        def __await__(self):
            async def _noop():
                return None

            return _noop().__await__()

        async def event_result(self, *, raise_if_any=False, raise_if_none=False):
            return self.outcome

    class _Bus:
        def __init__(self, outcome):
            self.outcome = outcome

        def dispatch(self, event):
            dispatched.append(event)
            return _Dispatch(self.outcome)

    class _Session:
        def __init__(self, outcome):
            self.event_bus = _Bus(outcome)

    def backend_for(outcome):
        instance = object.__new__(BrowserUseSessionBackend)
        instance.options = BrowserLaunchOptions(headless=True)
        instance.diagnostics = None
        instance._selector_map = {7: dom_node()}
        instance._state_revision = 1
        session = _Session(outcome)

        async def ensure_session():
            return session

        async def node_for_index(index):
            return instance._selector_map[int(index)]

        async def state_async():
            return "state"

        instance._ensure_session = ensure_session
        instance._node_for_index = node_for_index
        instance._state_async = state_async
        return instance

    ok = backend_for({"success": "true", "value": "b"})
    assert asyncio.run(ok._select_option_async(7, "Blue")) == "state"
    assert type(dispatched[-1]).__name__ == "SelectDropdownOptionEvent"
    assert getattr(dispatched[-1], "text") == "Blue"

    refused = backend_for({"success": "false"})
    with pytest.raises(BrowserError, match="did not match an option"):
        asyncio.run(refused._select_option_async(7, "Purple"))


@pytest.mark.parametrize("value", ["", "x" * 2001])
def test_select_still_validates_its_argument(value):
    import asyncio

    from app.agent_runtime.browser_backend import BrowserUseSessionBackend

    instance = object.__new__(BrowserUseSessionBackend)
    with pytest.raises(ValueError):
        asyncio.run(instance._select_option_async(0, value))
