from __future__ import annotations

import pytest

from app.agent_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.browser_transient import BrowserTransientInputPlatform
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


def test_transient_platform_uses_one_shot_opaque_reference():
    raw = "bare-password-without-a-label"
    delegate = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="type-1",
                        name="browser_type",
                        arguments={"browser_id": "b", "index": 2, "state_revision": 1, "text": raw},
                    ),
                )
            )
        ]
    )
    boundary = BrowserTransientInputPlatform(delegate)
    response = boundary.execute_chat("profile", object())
    stored = str(response.tool_calls[0].arguments["text"])

    assert raw not in stored
    assert stored.startswith("loom-transient-browser-text:")
    assert boundary.consume_browser_type_text(stored) == raw
    with pytest.raises(RuntimeError, match="no longer available"):
        boundary.consume_browser_type_text(stored)


def test_bare_typed_text_executes_but_never_reaches_durable_state(tmp_path):
    raw = "bare-password-without-a-label"
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

    platform.responses.extend(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="type-bare",
                        name="browser_type",
                        arguments={
                            "browser_id": browser.browser_id,
                            "index": 2,
                            "state_revision": snapshot.state_revision,
                            "text": raw,
                            "clear": True,
                        },
                    ),
                )
            ),
            ModelResponse(text="Typed."),
        ]
    )

    result = runtime.start_turn(session.session_id, "Type the provided value.")
    assert result.status is AgentStatus.COMPLETED
    assert typed == [raw]

    session_dir = store.session_dir(session.session_id)
    combined = (session_dir / "session.json").read_text(encoding="utf-8")
    combined += (session_dir / "events.jsonl").read_text(encoding="utf-8")
    assert raw not in combined
    assert "loom-transient-browser-text:" in combined
    runtime.close()


def test_secret_shaped_browser_url_is_scrubbed_and_blocked_before_durable_state(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="secret-url",
                        name="browser_navigate",
                        arguments={
                            "browser_id": "not-started",
                            "url": "https://example.com/callback?access_token=supersecret",
                        },
                    ),
                )
            ),
            ModelResponse(text="Secret-bearing browser URL was blocked."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")

    def must_not_start_backend(options):
        raise AssertionError("secret-bearing browser request must fail before backend access")

    runtime = BrowserRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        browser_backend_factory=must_not_start_backend,
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

    result = runtime.start_turn(session.session_id, "Navigate using this credential-bearing URL.")
    assert result.status is AgentStatus.COMPLETED

    session_dir = store.session_dir(session.session_id)
    combined = (session_dir / "session.json").read_text(encoding="utf-8")
    combined += (session_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "supersecret" not in combined
    assert "_loom_blocked_sensitive_input" in combined
    assert "Invalid tool request" in combined
    runtime.close()
