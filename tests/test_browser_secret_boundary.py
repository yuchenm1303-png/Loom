from __future__ import annotations

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
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


def test_secret_shaped_browser_arguments_are_scrubbed_before_durable_state(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="secret-type",
                        name="browser_type",
                        arguments={
                            "browser_id": "not-started",
                            "index": 2,
                            "state_revision": 1,
                            "text": "PASSWORD=hunter2",
                            "clear": True,
                        },
                    ),
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
            ModelResponse(text="Secret-bearing browser requests were blocked."),
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

    result = runtime.start_turn(session.session_id, "Use the browser with these credentials.")
    assert result.status is AgentStatus.COMPLETED

    session_dir = store.session_dir(session.session_id)
    durable_text = (session_dir / "session.json").read_text(encoding="utf-8")
    event_text = (session_dir / "events.jsonl").read_text(encoding="utf-8")
    combined = durable_text + event_text

    assert "hunter2" not in combined
    assert "supersecret" not in combined
    assert "PASSWORD=[REDACTED]" not in combined  # raw secret-shaped input is replaced, not retained as an executable value
    assert "REDACTED_SENSITIVE_INPUT" in combined
    assert "_loom_blocked_sensitive_input" in combined
    assert "Invalid tool request" in combined
    runtime.close()
