from __future__ import annotations

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse


class DummyPlatform:
    def execute_chat(self, profile_id, request):
        return ModelResponse(text="unused")


class InteractionBackend:
    backend_name = "interaction-fake"

    def __init__(self, options: BrowserLaunchOptions, calls: list[tuple]):
        self.options = options
        self.calls = calls
        self.state_revision = 0
        self.closed = False

    def _state(self) -> BrowserPageState:
        self.state_revision += 1
        return BrowserPageState(
            url="about:blank",
            title="Interaction fixture",
            dom=(
                "[1]<button aria-label='Menu'>Menu</button>\n"
                "[2]<select aria-label='Region'><option>US</option></select>"
            ),
            tabs=(),
        )

    def start(self) -> BrowserPageState:
        self.calls.append(("start",))
        return self._state()

    def state(self) -> BrowserPageState:
        self.calls.append(("state",))
        return self._state()

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        self.calls.append(("navigate", url, new_tab))
        return self._state()

    def click(self, index: int) -> BrowserPageState:
        self.calls.append(("click", index))
        return self._state()

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        self.calls.append(("type", index, text, clear))
        return self._state()

    def click_at(self, x: int, y: int, button: str = "left") -> BrowserPageState:
        self.calls.append(("click_at", x, y, button))
        return self._state()

    def send_text(self, text: str) -> BrowserPageState:
        self.calls.append(("send_text", text))
        return self._state()

    def hover(self, index: int) -> BrowserPageState:
        self.calls.append(("hover", index))
        return self._state()

    def press_key(self, key: str) -> BrowserPageState:
        self.calls.append(("press", key))
        return self._state()

    def select_option(self, index: int, value: str) -> BrowserPageState:
        self.calls.append(("select", index, value))
        return self._state()

    def drag(self, source_index: int, target_index: int) -> BrowserPageState:
        self.calls.append(("drag", source_index, target_index))
        return self._state()

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        self.calls.append(("scroll", direction, amount))
        return self._state()

    def go_back(self) -> BrowserPageState:
        self.calls.append(("back",))
        return self._state()

    def screenshot(self, *, full_page: bool = False) -> bytes:
        return b"\x89PNG\r\n\x1a\nFAKE"

    def close(self) -> None:
        self.closed = True
        self.calls.append(("close",))


def _runtime(tmp_path):
    calls: list[tuple] = []

    def factory(options: BrowserLaunchOptions):
        return InteractionBackend(options, calls)

    runtime = BrowserRuntime(
        platform=DummyPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        browser_backend_factory=factory,
        auto_configure_browser=False,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    return runtime, calls, workspace


def _context(workspace):
    return ToolContext(
        session_id="owner",
        turn_id="turn-browser-interactions",
        workspace=workspace,
        permission_mode="full-access",
    )


def test_richer_browser_tools_are_registered_sensitive_and_revision_scoped(tmp_path):
    runtime, calls, workspace = _runtime(tmp_path)
    store = runtime.browser_sessions
    assert store is not None
    managed = store.start("owner")
    context = _context(workspace)

    expected = {
        "browser_hover",
        "browser_press",
        "browser_select",
        "browser_drag",
        "browser_click_at",
        "browser_send_text",
    }
    for name in expected:
        tool = runtime.tools.get(name)
        assert tool is not None
        assert tool.effect is ToolEffect.SENSITIVE
        required = set(tool.input_schema.get("required", ()))
        assert {"browser_id", "state_revision"}.issubset(required)

    revision = store.snapshot("owner", managed.browser_id).state_revision
    hover = runtime.tools.get("browser_hover")
    assert hover is not None
    result = hover.handler(
        context,
        {"browser_id": managed.browser_id, "index": 1, "state_revision": revision},
    )
    assert ("hover", 1) in calls

    revision = int(result.data["state_revision"])
    press = runtime.tools.get("browser_press")
    assert press is not None
    result = press.handler(
        context,
        {"browser_id": managed.browser_id, "state_revision": revision, "key": "Enter"},
    )
    assert ("press", "Enter") in calls

    revision = int(result.data["state_revision"])
    select = runtime.tools.get("browser_select")
    assert select is not None
    result = select.handler(
        context,
        {"browser_id": managed.browser_id, "index": 2, "state_revision": revision, "value": "US"},
    )
    assert ("select", 2, "US") in calls

    revision = int(result.data["state_revision"])
    drag = runtime.tools.get("browser_drag")
    assert drag is not None
    result = drag.handler(
        context,
        {
            "browser_id": managed.browser_id,
            "source_index": 1,
            "target_index": 2,
            "state_revision": revision,
        },
    )
    assert ("drag", 1, 2) in calls

    revision = int(result.data["state_revision"])
    click_at = runtime.tools.get("browser_click_at")
    assert click_at is not None
    result = click_at.handler(
        context,
        {
            "browser_id": managed.browser_id,
            "x": 640,
            "y": 360,
            "button": "left",
            "state_revision": revision,
        },
    )
    assert ("click_at", 640, 360, "left") in calls

    revision = int(result.data["state_revision"])
    send_text = runtime.tools.get("browser_send_text")
    assert send_text is not None
    result = send_text.handler(
        context,
        {
            "browser_id": managed.browser_id,
            "state_revision": revision,
            "text": "echo loom\n",
        },
    )
    assert ("send_text", "echo loom\n") in calls
    assert int(result.data["state_revision"]) > revision
    runtime.close()


def test_extended_element_action_rejects_stale_revision_before_backend_call(tmp_path):
    """A stale revision never reaches the backend, and comes back recoverable.

    Refusing it is the invariant: the index means nothing against a page the
    model has not seen. What changed is the shape of the refusal. It used to
    raise a bare sentence telling the model to call browser_state and retry,
    which cost two further round trips - and in a real session the model spent
    the first of them repeating the identical call. The refusal now carries the
    re-read page, so the next turn can act instead of asking what changed.
    """

    runtime, calls, workspace = _runtime(tmp_path)
    store = runtime.browser_sessions
    assert store is not None
    managed = store.start("owner")
    stale = store.snapshot("owner", managed.browser_id).state_revision
    store.state("owner", managed.browser_id)

    hover = runtime.tools.get("browser_hover")
    assert hover is not None
    result = hover.handler(
        _context(workspace),
        {"browser_id": managed.browser_id, "index": 1, "state_revision": stale},
    )
    assert result.ok is False
    assert "stale browser state_revision" in result.content
    # The whole point of attaching it: the model can pick an index straight away.
    assert int(result.data["state_revision"]) > stale
    assert "dom" in result.data
    assert ("hover", 1) not in calls
    runtime.close()


def test_a_replaced_element_comes_back_with_the_page_already_re_read(tmp_path):
    """The most common browser failure there is: 18 of 104 clicks in real logs.

    The element id is stamped on the DOM node when the page is captured, so any
    re-render invalidates it even though the button is still on screen in the
    same place. The old failure was one sentence - call browser_state and retry
    - which cost three round trips: 35 seconds of wall clock for 0.55 seconds of
    browser work, with the model spending the first of them repeating the
    identical call. Re-reading is the cheap part and now travels with the error.

    It stays a failure. Retrying the same index against a re-rendered page is how
    a click lands on the wrong element, so choosing again is the model's job.
    """

    runtime, calls, workspace = _runtime(tmp_path)
    store = runtime.browser_sessions
    assert store is not None
    managed = store.start("owner")
    item = store._owned("owner", managed.browser_id)
    revision = store.snapshot("owner", managed.browser_id).state_revision

    def stale_click(index: int):
        calls.append(("click", index))
        raise BrowserError("Element is no longer available. Refresh browser_state and retry.")

    item.backend.click = stale_click

    click = runtime.tools.get("browser_click")
    result = click.handler(
        _context(workspace),
        {"browser_id": managed.browser_id, "index": 1, "state_revision": revision},
    )

    assert result.ok is False
    assert "no longer available" in result.content
    assert "state_revision" in result.content
    # The recovery the model would otherwise have spent a turn asking for.
    assert int(result.data["state_revision"]) > revision
    assert result.data["dom"]
    assert ("state",) in calls
    runtime.close()


def test_an_unrelated_browser_failure_is_not_dressed_up_as_a_stale_view(tmp_path):
    """Only a stale view earns the re-read; everything else raises as before."""

    runtime, calls, workspace = _runtime(tmp_path)
    store = runtime.browser_sessions
    managed = store.start("owner")
    item = store._owned("owner", managed.browser_id)
    revision = store.snapshot("owner", managed.browser_id).state_revision

    def broken_click(index: int):
        raise BrowserError("net::ERR_CONNECTION_REFUSED")

    item.backend.click = broken_click

    click = runtime.tools.get("browser_click")
    with pytest.raises(BrowserError, match="ERR_CONNECTION_REFUSED"):
        click.handler(
            _context(workspace),
            {"browser_id": managed.browser_id, "index": 1, "state_revision": revision},
        )
    runtime.close()


def test_extended_tool_reports_backend_capability_gap(tmp_path):
    runtime, _, workspace = _runtime(tmp_path)
    store = runtime.browser_sessions
    assert store is not None
    managed = store.start("owner")
    item = store._owned("owner", managed.browser_id)
    item.backend.hover = None
    revision = store.snapshot("owner", managed.browser_id).state_revision

    hover = runtime.tools.get("browser_hover")
    assert hover is not None
    with pytest.raises(RuntimeError, match="does not support hover"):
        hover.handler(
            _context(workspace),
            {"browser_id": managed.browser_id, "index": 1, "state_revision": revision},
        )
    runtime.close()
