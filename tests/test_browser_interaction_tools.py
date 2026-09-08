from __future__ import annotations

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
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

    expected = {"browser_hover", "browser_press", "browser_select", "browser_drag"}
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
    assert int(result.data["state_revision"]) > revision
    runtime.close()


def test_extended_element_action_rejects_stale_revision_before_backend_call(tmp_path):
    runtime, calls, workspace = _runtime(tmp_path)
    store = runtime.browser_sessions
    assert store is not None
    managed = store.start("owner")
    stale = store.snapshot("owner", managed.browser_id).state_revision
    store.state("owner", managed.browser_id)

    hover = runtime.tools.get("browser_hover")
    assert hover is not None
    with pytest.raises(RuntimeError, match="stale browser state_revision"):
        hover.handler(
            _context(workspace),
            {"browser_id": managed.browser_id, "index": 1, "state_revision": stale},
        )
    assert ("hover", 1) not in calls
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
