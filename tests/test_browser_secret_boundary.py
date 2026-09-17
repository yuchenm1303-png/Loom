from __future__ import annotations

import pytest

from app.agent_runtime import BrowserRuntime
from app.agent_runtime.browser_runtime import _sanitize_browser_tool_call
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.contracts import AgentStatus, PermissionMode
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, profile_id, request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class RecordingBrowserBackend:
    backend_name = "recording-browser"

    def __init__(self, options: BrowserLaunchOptions, typed: list[str]):
        self.options = options
        self.typed = typed
        self.state_revision = 0

    def _state(self) -> BrowserPageState:
        self.state_revision += 1
        return BrowserPageState(
            url="about:blank",
            title="Input test",
            dom="[2]<input name=query />",
            tabs=({"tab_id": "tab-main", "url": "about:blank", "title": "Input test"},),
        )

    def start(self):
        return self._state()

    def state(self):
        return self._state()

    def navigate(self, url, *, new_tab=False):
        return self._state()

    def click(self, index):
        return self._state()

    def type_text(self, index, text, *, clear=True):
        self.typed.append(text)
        return self._state()

    def scroll(self, direction, amount):
        return self._state()

    def go_back(self):
        return self._state()

    def screenshot(self, *, full_page=False):
        return b"\x89PNG\r\n\x1a\n"

    def close(self):
        return None


def test_secret_shaped_browser_type_text_is_a_normal_tool_argument(tmp_path):
    raw = "password=hunter2"
    typed: list[str] = []
    store = FileAgentSessionStore(tmp_path / "state")
    platform = ScriptedPlatform([])

    def factory(options):
        return RecordingBrowserBackend(options, typed)

    runtime = BrowserRuntime(
        platform=platform,
        store=store,
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
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    assert runtime.browser_sessions is not None
    browser = runtime.browser_sessions.start(session.session_id)
    snapshot = runtime.browser_sessions.snapshot(session.session_id, browser.browser_id)
    expected_arguments = {
        "browser_id": browser.browser_id,
        "index": 2,
        "state_revision": snapshot.state_revision,
        "text": raw,
        "clear": True,
    }

    platform.responses.extend(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="type-secret-shaped",
                        name="browser_type",
                        arguments=expected_arguments,
                    ),
                )
            ),
            ModelResponse(text="Typed."),
        ]
    )

    result = runtime.start_turn(session.session_id, "Type the provided value.")
    assert result.status is AgentStatus.COMPLETED
    assert typed == [raw]

    stored = store.load(session.session_id)
    persisted_calls = [
        call
        for message in stored.messages
        for call in message.tool_calls
        if call.name == "browser_type"
    ]
    assert persisted_calls
    assert persisted_calls[-1].arguments == expected_arguments

    session_dir = store.session_dir(session.session_id)
    combined = (session_dir / "session.json").read_text(encoding="utf-8")
    combined += (session_dir / "events.jsonl").read_text(encoding="utf-8")
    assert raw in combined
    assert "[REDACTED_SENSITIVE_INPUT]" not in combined
    assert "_loom_blocked_sensitive_input" not in combined
    runtime.close()


@pytest.mark.parametrize("tool_name", ["browser_open", "browser_navigate"])
def test_secret_shaped_browser_urls_are_still_redacted_and_blocked(tool_name):
    arguments = {"url": "https://example.com/callback?access_token=supersecret"}
    if tool_name == "browser_navigate":
        arguments["browser_id"] = "browser-1"

    sanitized = _sanitize_browser_tool_call(
        ToolCall(call_id=f"{tool_name}-secret-url", name=tool_name, arguments=arguments)
    )

    assert "supersecret" not in str(sanitized.arguments["url"])
    assert "REDACTED" in str(sanitized.arguments["url"])
    assert sanitized.arguments["_loom_blocked_sensitive_input"] is True
