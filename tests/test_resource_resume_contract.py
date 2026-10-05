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
        for _ in range(3):
            runtime.start_turn(session.session_id, "Continue")
        releases = [e for e in runtime.store.events(session.session_id) if "browser_resources" in e.data]
        assert len(releases) == 1, [(e.kind.value, e.data) for e in runtime.store.events(session.session_id)
                                    if e.kind in {AgentEventKind.TOOL_FAILED, AgentEventKind.TURN_FAILED}]
        resources = releases[0].data["browser_resources"]
        assert resources["count"] == 1
        opened = next(e.data for e in runtime.store.events(session.session_id)
                      if e.kind is AgentEventKind.TOOL_COMPLETED and e.data.get("call_id") == "open")
        assert resources["browser_ids"] == [opened["data"]["browser_id"]]
        assert all(r.messages[0].name == "" for r in platform.requests)
        resume_counts = [sum(m.name == "loom_resource_resume" for m in r.messages) for r in platform.requests]
        assert resume_counts == [0, 0, 1, 0, 0]
        resume = next(m for m in platform.requests[2].messages if m.name == "loom_resource_resume")
        assert platform.requests[2].messages[-1] == resume
        assert json.loads(resume.content.split("\n", 1)[1])["browser_resources"] == resources
    finally:
        runtime.close()
