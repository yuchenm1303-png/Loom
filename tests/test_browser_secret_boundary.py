from __future__ import annotations

import pytest

from app.agent_runtime import BrowserRuntime
from app.agent_runtime.tools import ToolContext
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


def test_a_retired_transient_placeholder_is_refused_instead_of_typed(tmp_path):
    """Observed on a real GitHub form: the placeholder was typed into the field.

    Conversations from before the transient boundary was removed still carry
    `loom-transient-browser-text:` references, and a model reading its own history
    copies them into the text argument. Nothing resolves them any more, so they
    used to be entered verbatim while browser_type reported success - a silent
    wrong value, which is worse than the loop the removal was meant to fix.
    """

    typed: list[str] = []
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = BrowserRuntime(
        platform=ScriptedPlatform([]),
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        browser_backend_factory=lambda options: RecordingBrowserBackend(options, typed),
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

    tool = runtime.tools.get("browser_type")
    assert tool is not None
    context = ToolContext(session_id=session.session_id, turn_id="t", workspace=workspace)
    with pytest.raises(ValueError, match="retired internal placeholder"):
        tool.handler(
            context,
            {
                "browser_id": browser.browser_id,
                "index": 2,
                "state_revision": snapshot.state_revision,
                "text": "loom-transient-browser-text:5050505050505050505050505050505",
            },
        )

    assert typed == [], "the placeholder reached the page instead of being refused"
    runtime.close()


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
    """The sanitizer redacts the URL and makes the call deliberately schema-invalid.

    This covers the rule itself for both tools. It cannot tell whether anything
    still calls the sanitizer, which is what the end-to-end test below is for.
    """

    arguments = {"url": "https://example.com/callback?access_token=supersecret"}
    if tool_name == "browser_navigate":
        arguments["browser_id"] = "browser-1"

    sanitized = _sanitize_browser_tool_call(
        ToolCall(call_id=f"{tool_name}-secret-url", name=tool_name, arguments=arguments)
    )

    assert "supersecret" not in str(sanitized.arguments["url"])
    assert "REDACTED" in str(sanitized.arguments["url"])
    assert sanitized.arguments["_loom_blocked_sensitive_input"] is True


def test_secret_shaped_browser_url_is_scrubbed_and_blocked_before_durable_state(tmp_path):
    """The sanitizer is wired into the runtime, not merely present in the module.

    Removing the browser_type branch left this rule as the only caller of
    _sanitize_browser_tool_call, and unit-testing the function alone cannot see
    whether _BrowserSecretBoundaryPlatform still wraps the platform. Detaching
    that wrapper - which would persist a live credential and navigate with it -
    kept every other test in this file green, so the wiring is asserted here by
    driving a real turn: the secret must not reach durable state, the call must
    be rejected as invalid, and the backend must never be reached.
    """

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
