"""Resource metadata must describe real releases, on the production runtime."""
import json

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserPageState
from app.agent_runtime.contracts import AgentEventKind
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse, ToolCall
from test_agent_request_layout import ScriptedPlatform


class Browser:
    def __init__(self, options):
        self.closed = False

    def start(self):
        return BrowserPageState(url="https://example.test", title="Fixture", dom="fixture")

    def navigate(self, url, *, new_tab=False):
        return self.start()

    def close(self):
        self.closed = True


def runtime_for(tmp_path, responses):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    platform = ScriptedPlatform(responses)
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        browser_backend_factory=Browser, auto_configure_browser=False,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False))
    session = runtime.create_session("agent.fast", workspace_dir=workspace, permission_mode="full-access")
    return runtime, session, platform


def test_browser_free_three_turns_never_report_released_resources(tmp_path):
    runtime, session, platform = runtime_for(tmp_path, [ModelResponse(text="Done")] * 3)
    try:
        for _ in range(3):
            runtime.start_turn(session.session_id, "Hello")
        assert all(request.messages[0].name == "" for request in platform.requests)
        assert not any(m.name == "loom_resource_resume" for r in platform.requests for m in r.messages)
        assert not any("browser_resources" in e.data for e in runtime.store.events(session.session_id))
    finally:
        runtime.close()


def test_real_release_is_reported_only_in_next_turn_first_request(tmp_path):
    runtime, session, platform = runtime_for(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("open", "browser_open", {"url": "https://example.test"}),)),
        ModelResponse(text="Done"),
        ModelResponse(tool_calls=(ToolCall("list", "list_workspace_files", {}),)),
        ModelResponse(text="Done"), ModelResponse(text="Done"),
    ])
    try:
        runtime.start_turn(session.session_id, "Continue")
        opened = next(e.data for e in runtime.store.events(session.session_id)
                      if e.kind is AgentEventKind.TOOL_COMPLETED and e.data.get("call_id") == "open")
        assert runtime.browser_sessions.active_count() == 1
        runtime.release_session_resources(session.session_id, reason="explicit_close")
        runtime.start_turn(session.session_id, "Continue")
        runtime.start_turn(session.session_id, "Continue")
        releases = [e for e in runtime.store.events(session.session_id)
                    if e.kind is AgentEventKind.BROWSER_SESSION_RELEASED]
        assert len(releases) == 1
        assert releases[0].data["browser_id"] == opened["data"]["browser_id"]
        assert releases[0].data["reason"] == "explicit_close"
        assert all(r.messages[0].name == "" for r in platform.requests)
        resume_counts = [sum(m.name == "loom_resource_resume" for m in r.messages) for r in platform.requests]
        assert resume_counts == [0, 0, 1, 1, 1]
        for previous, following in zip(platform.requests[2:], platform.requests[3:]):
            assert following.messages[:len(previous.messages)] == previous.messages
        resume = next(m for m in platform.requests[2].messages if m.name == "loom_resource_resume")
        assert all(next(m for m in r.messages if m.name == "loom_resource_resume") == resume
                   for r in platform.requests[2:])
        resources = json.loads(resume.content.split("\n", 1)[1])["browser_resources"]
        assert resources["browser_ids"] == [opened["data"]["browser_id"]]
        assert resources["releases"][0]["event_id"] == releases[0].event_id
    finally:
        runtime.close()
