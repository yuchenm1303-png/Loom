from __future__ import annotations

import json

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime, BrowserSessionStore, redact_browser_url
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState, BrowserURLPolicyError
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class FakeBrowserBackend:
    backend_name = "fake-browser"

    def __init__(self, options: BrowserLaunchOptions, calls: list[tuple]):
        self.options = options
        self.calls = calls
        self.closed = False
        self.state_revision = 0
        self.current_url = "about:blank"
        self.active_tab = "tab-main"
        self._tabs = ["tab-main"]

    def _state(self) -> BrowserPageState:
        self.state_revision += 1
        tabs = tuple(
            {
                "tab_id": tab,
                "url": self.current_url if tab == self.active_tab else "https://example.com/other",
                "title": "Fake page",
            }
            for tab in self._tabs
        )
        return BrowserPageState(
            url=self.current_url,
            title="Fake page",
            dom="[1]<button>Continue</button>\n[2]<input name=query />",
            tabs=tabs,
            page_info={"fake": True},
        )

    def start(self) -> BrowserPageState:
        self.calls.append(("start", self.options.allowed_domains))
        return self._state()

    def state(self) -> BrowserPageState:
        self.calls.append(("state",))
        return self._state()

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        self.calls.append(("navigate", url, new_tab))
        if new_tab:
            tab = f"tab-{len(self._tabs) + 1}"
            self._tabs.append(tab)
            self.active_tab = tab
        self.current_url = url
        return self._state()

    def click(self, index: int) -> BrowserPageState:
        self.calls.append(("click", index))
        if index not in {1, 2}:
            raise RuntimeError("missing element")
        return self._state()

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        self.calls.append(("type", index, clear, len(text)))
        return self._state()

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        self.calls.append(("scroll", direction, amount))
        return self._state()

    def go_back(self) -> BrowserPageState:
        self.calls.append(("back",))
        return self._state()

    def refresh(self) -> BrowserPageState:
        self.calls.append(("refresh",))
        return self._state()

    def tabs(self) -> BrowserPageState:
        self.calls.append(("tabs",))
        return self._state()

    def switch_tab(self, tab_id: str) -> BrowserPageState:
        self.calls.append(("switch_tab", tab_id))
        if tab_id not in self._tabs:
            raise RuntimeError("missing tab")
        self.active_tab = tab_id
        return self._state()

    def close_tab(self, tab_id: str) -> BrowserPageState:
        self.calls.append(("close_tab", tab_id))
        if tab_id not in self._tabs:
            raise RuntimeError("missing tab")
        self._tabs.remove(tab_id)
        if not self._tabs:
            self._tabs.append("tab-main")
            self.current_url = "about:blank"
        self.active_tab = self._tabs[-1]
        return self._state()

    def screenshot(self, *, full_page: bool = False) -> bytes:
        self.calls.append(("screenshot", full_page))
        return b"\x89PNG\r\n\x1a\nFAKE"

    def close(self) -> None:
        self.calls.append(("close",))
        self.closed = True


def _factory(calls, created):
    def build(options):
        backend = FakeBrowserBackend(options, calls)
        created.append(backend)
        return backend

    return build


def _runtime(tmp_path, responses, *, mode=PermissionMode.APPROVAL):
    calls: list[tuple] = []
    created: list[FakeBrowserBackend] = []
    platform = ScriptedPlatform(responses)
    runtime = BrowserRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        browser_backend_factory=_factory(calls, created),
        auto_configure_browser=False,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=mode,
    )
    return runtime, platform, session, calls, created, workspace


def test_security_policy_blocks_local_and_obfuscated_ip_forms():
    policy = BrowserSecurityPolicy(resolve_dns=False)
    blocked = (
        "http://127.0.0.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://%31%32%37.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
    )
    for url in blocked:
        with pytest.raises(BrowserURLPolicyError):
            policy.validate(url)

    assert policy.validate("https://8.8.8.8/") == "https://8.8.8.8/"


def test_security_policy_enforces_allowed_domains_and_credentials():
    policy = BrowserSecurityPolicy(resolve_dns=False)
    assert policy.validate(
        "https://docs.example.com/a", allowed_domains=("*.example.com",)
    ).startswith("https://docs.example.com")
    with pytest.raises(BrowserURLPolicyError):
        policy.validate("https://example.com/", allowed_domains=("*.example.com",))
    with pytest.raises(BrowserURLPolicyError):
        policy.validate("https://evil.example.net/", allowed_domains=("example.com",))
    with pytest.raises(BrowserURLPolicyError):
        policy.validate("https://user:password@example.com/")


def test_browser_state_redacts_secret_shaped_url_values():
    safe = redact_browser_url(
        "https://example.com/callback?access_token=topsecret&next=%2Fhome#id_token=header.payload.signature"
    )
    assert "topsecret" not in safe
    assert "header.payload.signature" not in safe
    assert "%5BREDACTED%5D" in safe or "[REDACTED]" in safe


def test_unconfigured_runtime_exposes_status_only(tmp_path):
    platform = ScriptedPlatform([ModelResponse(text="done")])
    runtime = BrowserRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=False,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace, permission_mode="full-access")
    result = runtime.start_turn(session.session_id, "Report browser availability.")
    names = {tool.name for tool in platform.requests[0][1].tools}
    assert result.status is AgentStatus.COMPLETED
    assert "browser_status" in names
    assert "browser_open" not in names
    assert runtime.browser_status(session.session_id)["enabled"] is False
    runtime.close()


def test_browser_open_requires_approval_before_process_start(tmp_path):
    runtime, platform, session, calls, created, _ = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="browser-open-1",
                        name="browser_open",
                        arguments={"url": "https://example.com/", "allowed_domains": ["example.com"]},
                    ),
                )
            ),
            ModelResponse(text="Browser opened."),
        ],
    )

    first = runtime.start_turn(session.session_id, "Open example.com in the browser.")
    assert first.status is AgentStatus.WAITING_APPROVAL
    assert first.pending_approval is not None
    assert first.pending_approval.tool_name == "browser_open"
    assert created == []
    assert calls == []

    result = runtime.resume_approval(session.session_id, "browser-open-1", approved=True)
    assert result.status is AgentStatus.COMPLETED
    assert len(created) == 1
    assert calls[0][0] == "start"
    assert ("navigate", "https://example.com/", False) in calls
    names = {tool.name for tool in platform.requests[0][1].tools}
    assert {"browser_status", "browser_open", "browser_state", "browser_click", "browser_type"}.issubset(names)
    runtime.close()


def test_read_only_denies_browser_without_starting_backend(tmp_path):
    runtime, _, session, calls, created, _ = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="browser-denied", name="browser_open", arguments={}),)
            ),
            ModelResponse(text="Browser access is denied."),
        ],
        mode=PermissionMode.READ_ONLY,
    )
    result = runtime.start_turn(session.session_id, "Open a browser.")
    assert result.status is AgentStatus.COMPLETED
    assert calls == []
    assert created == []
    runtime.close()


def test_snapshot_revision_fails_closed_after_refresh():
    calls: list[tuple] = []
    created: list[FakeBrowserBackend] = []
    store = BrowserSessionStore(
        _factory(calls, created),
        url_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    item = store.start("owner", allowed_domains=("example.com",))
    first = store.snapshot("owner", item.browser_id)
    store.ensure_revision("owner", item.browser_id, first.state_revision)
    store.navigate("owner", item.browser_id, "https://example.com/")
    with pytest.raises(RuntimeError, match="stale browser state_revision"):
        store.ensure_revision("owner", item.browser_id, first.state_revision)
    store.close_all()


def test_type_tool_does_not_echo_typed_secret_and_screenshot_bytes_stay_out_of_result(tmp_path):
    runtime, _, session, _, _, workspace = _runtime(tmp_path, [ModelResponse(text="unused")], mode=PermissionMode.FULL_ACCESS)
    store = runtime.browser_sessions
    assert store is not None
    item = store.start(session.session_id)
    snapshot = store.snapshot(session.session_id, item.browser_id)
    context = ToolContext(
        session_id=session.session_id,
        turn_id="turn-test",
        workspace=workspace,
        permission_mode="full-access",
    )

    type_tool = runtime.tools.get("browser_type")
    assert type_tool is not None and type_tool.effect is ToolEffect.SENSITIVE
    typed = type_tool.handler(
        context,
        {
            "browser_id": item.browser_id,
            "index": 2,
            "state_revision": snapshot.state_revision,
            "text": "PASSWORD=hunter2",
            "clear": True,
        },
    )
    serialized = json.dumps({"content": typed.content, "data": typed.data})
    assert "hunter2" not in serialized

    screenshot_tool = runtime.tools.get("browser_screenshot")
    assert screenshot_tool is not None
    shot = screenshot_tool.handler(context, {"browser_id": item.browser_id})
    assert "FAKE" not in json.dumps({"content": shot.content, "data": shot.data})
    shot_path = workspace / str(shot.data["path"])
    assert shot_path.read_bytes().startswith(b"\x89PNG")
    runtime.close()


def test_permission_transition_closes_owned_browser_sessions(tmp_path):
    runtime, _, session, calls, _, _ = _runtime(tmp_path, [ModelResponse(text="unused")], mode=PermissionMode.FULL_ACCESS)
    assert runtime.browser_sessions is not None
    runtime.browser_sessions.start(session.session_id)
    assert runtime.browser_status(session.session_id)["active_sessions"] == 1
    runtime.set_permission_mode(session.session_id, PermissionMode.READ_ONLY)
    assert runtime.browser_status(session.session_id)["active_sessions"] == 0
    assert ("close",) in calls
    runtime.close()


def test_browser_actions_are_sensitive_except_status(tmp_path):
    runtime, _, _, _, _, _ = _runtime(tmp_path, [ModelResponse(text="unused")], mode=PermissionMode.FULL_ACCESS)
    for tool in runtime.tools.all():
        if not tool.name.startswith("browser_"):
            continue
        if tool.name == "browser_status":
            assert tool.effect is ToolEffect.READ_ONLY
        else:
            assert tool.effect is ToolEffect.SENSITIVE
    runtime.close()
