from __future__ import annotations

from pathlib import Path

from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import (
    BrowserLaunchOptions,
    BrowserPageState,
    BrowserSessionManager,
    BrowserURLPolicy,
)
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolExposure
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse, ToolCall


class TypePlatform:
    def execute_chat(self, profile_id, request):
        return ModelResponse(
            text="",
            tool_calls=(
                ToolCall(
                    call_id="type-1",
                    name="browser_type",
                    arguments={
                        "browser_id": "browser-1",
                        "state_revision": 1,
                        "index": 2,
                        "text": "secret-value-that-must-stay-in-ram",
                    },
                ),
            ),
        )


class MinimalBackend:
    backend_name = "cleanup-fake"

    def __init__(self, options: BrowserLaunchOptions):
        self.options = options
        self.state_revision = 0

    def _state(self) -> BrowserPageState:
        self.state_revision += 1
        return BrowserPageState(
            url="https://example.com/",
            title="Example",
            dom="[1]<button>Go</button>",
        )

    def start(self) -> BrowserPageState:
        return self._state()

    def state(self) -> BrowserPageState:
        return self._state()

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        return self._state()

    def click(self, index: int) -> BrowserPageState:
        return self._state()

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        return self._state()

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        return self._state()

    def go_back(self) -> BrowserPageState:
        return self._state()

    def screenshot(self, *, full_page: bool = False) -> bytes:
        return b"\x89PNG\r\n\x1a\nFAKE"

    def close(self) -> None:
        return None


def _runtime(tmp_path: Path) -> BrowserRuntime:
    return BrowserRuntime(
        platform=TypePlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        browser_backend_factory=lambda options: MinimalBackend(options),
        auto_configure_browser=False,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )


def test_browser_transient_input_is_composed_before_durable_secret_boundary(tmp_path):
    runtime = _runtime(tmp_path)
    response = runtime.platform.execute_chat("test", object())
    call = response.tool_calls[0]
    value = str(call.arguments["text"])

    assert value.startswith("loom-transient-browser-text:")
    assert "secret-value-that-must-stay-in-ram" not in value
    assert runtime.consume_browser_type_text(value) == "secret-value-that-must-stay-in-ram"
    runtime.close()


def test_advanced_browser_tools_are_deferred_but_still_discoverable(tmp_path):
    runtime = _runtime(tmp_path)

    direct = {tool.name for tool in runtime.tools.router().all()}
    deferred = {tool.name for tool in runtime.tools.deferred()}

    for name in {
        "browser_open",
        "browser_state",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_select",
        "browser_screenshot",
    }:
        assert name in direct

    for name in {
        "browser_eval",
        "browser_network",
        "browser_cookies",
        "browser_storage",
        "browser_session_state",
        "browser_emulate",
    }:
        tool = runtime.tools.get(name)
        assert tool is not None
        assert tool.exposure is ToolExposure.DEFERRED
        assert name not in direct
        assert name in deferred

    matches = runtime.tools.search_deferred("browser eval javascript", limit=5)
    assert any(tool.name == "browser_eval" for tool in matches)
    runtime.close()


def test_legacy_browser_url_policy_name_uses_canonical_security_policy():
    legacy = BrowserURLPolicy(resolve_dns=False)
    assert isinstance(legacy, BrowserSecurityPolicy)
    assert legacy.validate("https://example.com/") == "https://example.com/"


def test_browser_session_manager_default_policy_is_canonical():
    manager = BrowserSessionManager(
        lambda options: MinimalBackend(options),
    )
    assert isinstance(manager.url_policy, BrowserSecurityPolicy)


def test_browser_status_reports_deferred_surface(tmp_path):
    runtime = _runtime(tmp_path)
    status = runtime.browser_status()
    assert "advanced browser tools deferred" in status["default_tool_surface"]
    assert "browser_eval" in status["deferred_browser_tools"]
    runtime.close()
