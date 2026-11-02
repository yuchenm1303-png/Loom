"""browser_batch: several actions from one state, each element found again by what it is.

Live browser suite (2026-11-01): every single-action browser call is a model round
trip and its indexes only fit its own state, so a login form cost three. These pin
that a batch runs in order, re-finds renumbered elements, stops - the rest not
executed - when an element is gone or ambiguous or a step fails, and validates
everything before the first action.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.browser_tools import _element_descriptors, _relocate
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse

# The real browser-use format (dom_probe on the suite's login page, 2026-11-01):
# form fields carry a "|SHADOW(open)|" prefix, which an index pattern anchored at
# "[" missed - every live batch failed with "index 2 is not in this state".
LOGIN = (
    "Sign in\n"
    "Username\n"
    "|SHADOW(open)|[2]<input id=u autocomplete=off />\n"
    "Password\n"
    "|SHADOW(open)|[3]<input id=p type=password />\n"
    "[5]<button id=go />\n"
    "\tSign in\n"
)


class Platform:
    def execute_chat(self, profile_id, request):
        return ModelResponse(text="unused")


class PageBackend:
    backend_name = "batch-fake"

    def __init__(self, options, calls, pages):
        self.options = options
        self.calls = calls
        self.pages = pages  # dom after each action, in order; the last one repeats
        self.state_revision = 0
        self.fail_on = None

    def _state(self):
        self.state_revision += 1
        dom = self.pages[min(len(self.calls), len(self.pages) - 1)]
        return BrowserPageState(url="https://example.com/login", title="Sign in", dom=dom, tabs=())

    def start(self):
        return self._state()

    def state(self):
        return self._state()

    def _act(self, *call):
        if self.fail_on is not None and call[0] == self.fail_on:
            raise BrowserError("Target element is covered by another element")
        self.calls.append(call)
        return self._state()

    def click(self, index):
        return self._act("click", index)

    def type_text(self, index, text, *, clear=True):
        return self._act("type", index, text)

    def press_key(self, key):
        return self._act("press", key)

    def select_option(self, index, value):
        return self._act("select", index, value)

    def scroll(self, direction, amount):
        return self._act("scroll", direction, amount)

    def navigate(self, url, *, new_tab=False):
        return self._state()

    def screenshot(self, *, full_page=False):
        return b"\x89PNG\r\n\x1a\n"

    def close(self):
        pass


@pytest.fixture
def rig(tmp_path):
    calls: list[tuple] = []
    state = {"pages": [LOGIN], "backend": None}

    def factory(options: BrowserLaunchOptions):
        state["backend"] = PageBackend(options, calls, state["pages"])
        return state["backend"]

    runtime = BrowserRuntime(
        platform=Platform(), store=FileAgentSessionStore(tmp_path / "state"), tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF), web_search_provider=None,
        auto_configure_web_search=False, browser_backend_factory=factory, auto_configure_browser=False,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False))
    workspace = tmp_path / "project"
    workspace.mkdir()
    managed = runtime.browser_sessions.start("owner")
    context = ToolContext(session_id="owner", turn_id="turn", workspace=workspace, permission_mode="full-access")
    tool = runtime.tools.get("browser_batch")

    def run(actions, revision=None):
        snapshot = runtime.browser_sessions.snapshot("owner", managed.browser_id)
        return tool.handler(context, {"browser_id": managed.browser_id,
                                      "state_revision": revision or snapshot.state_revision, "actions": actions})

    yield type("Rig", (), {"run": staticmethod(run), "calls": calls, "state": state, "runtime": runtime})
    runtime.close()


SIGN_IN = [{"type": "type", "index": 2, "text": "tester"}, {"type": "type", "index": 3, "text": "orange-42"},
           {"type": "click", "index": 5}]


def test_a_login_form_runs_as_one_call(rig):
    result = rig.run(SIGN_IN)

    assert result.ok is True
    assert rig.calls == [("type", 2, "tester"), ("type", 3, "orange-42"), ("click", 5)]
    assert result.data["batch"]["executed"] == 3


def test_renumbered_elements_are_found_again_by_what_they_are(rig):
    renumbered = LOGIN.replace("[3]", "[13]").replace("[5]", "[15]")
    rig.state["pages"][:] = [LOGIN, renumbered, renumbered, renumbered]

    result = rig.run(SIGN_IN)

    assert result.ok is True
    assert rig.calls == [("type", 2, "tester"), ("type", 13, "orange-42"), ("click", 15)]


def test_a_vanished_element_stops_the_batch_before_it(rig):
    gone = LOGIN.replace("[5]<button id=go />\n\tSign in\n", "Please wait\n")
    rig.state["pages"][:] = [LOGIN, gone]

    result = rig.run(SIGN_IN)

    assert result.ok is False
    assert rig.calls == [("type", 2, "tester"), ("type", 3, "orange-42")]  # the password field is still there
    assert result.data["batch"]["executed"] == 2
    assert "remaining actions were not executed" in result.content


def test_a_failing_step_stops_the_rest(rig):
    rig.run([{"type": "type", "index": 2, "text": "x"}])  # starts the backend
    rig.state["backend"].fail_on = "click"
    rig.calls.clear()

    result = rig.run([{"type": "type", "index": 3, "text": "y"}, {"type": "click", "index": 5},
                      {"type": "press", "key": "Enter"}])

    assert result.ok is False and "action 2 failed" in result.data["batch"]["stopped"]
    assert rig.calls == [("type", 3, "y")]


@pytest.mark.parametrize("actions, message", [
    ([{"type": "click"}], "needs an element index"),
    ([{"type": "type", "index": 2}], "needs text"),
    ([{"type": "hover", "index": 2}], "type must be one of"),
    ([{"type": "click", "index": 99}], "not in this state"),
    ([{"type": "press", "key": " "}], "needs a key"),
])
def test_a_malformed_batch_executes_nothing(rig, actions, message):
    with pytest.raises(ValueError, match=message):
        rig.run([{"type": "type", "index": 2, "text": "ok"}, *actions])
    assert rig.calls == []


def test_a_stale_revision_executes_nothing(rig):
    result = rig.run(SIGN_IN, revision=999)
    assert result.ok is False and rig.calls == []


def test_descriptors_ignore_the_number_and_refuse_ambiguity():
    planned = _element_descriptors(LOGIN)
    assert planned[5] == "<button id=go /> Sign in"
    twins = "[7]<button id=go />\n\tSign in\n[8]<button id=go />\n\tSign in\n"
    assert _relocate(planned[5], 5, _element_descriptors(twins)) is None
    assert _relocate(planned[5], 5, _element_descriptors("[9]<button id=go />\n\tSign in\n")) == 9


def test_new_element_markers_are_read_too():
    assert _element_descriptors("*[7]<button id=ok />\n\tOK\n") == {7: "<button id=ok /> OK"}


EXTENSION_LOGIN = (
    "[0] <input> placeholder=\"Username\" rect=10,40,200x24\n"
    "[1] <input> type=\"password\" rect=10,80,200x24\n"
    "[2] <button> text=\"Sign in\" rect=10,120,80x24\n"
)


def test_the_extension_format_is_read_without_position_or_value():
    planned = _element_descriptors(EXTENSION_LOGIN)
    assert planned == {0: '<input> placeholder="Username"', 1: '<input> type="password"', 2: '<button> text="Sign in"'}
    typed = ("[0] <input> placeholder=\"Username\" value=\"tester\" rect=10,30,200x24\n"
             "[1] <input> type=\"password\" filled=\"true\" value-withheld=\"password\" rect=10,70,200x24\n"
             "[2] <button> text=\"Sign in\" rect=10,110,80x24\n")
    current = _element_descriptors(typed)
    assert [_relocate(planned[i], i, current) for i in range(3)] == [0, 1, 2]
