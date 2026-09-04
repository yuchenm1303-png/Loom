from __future__ import annotations

from app.agent_runtime import AgentEventKind, AgentRuntime, AgentStatus, FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    ModelBinding,
    ModelResponse,
    ModelUsage,
    ProviderAdapter,
    ProviderConnection,
    ToolCall,
)


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_generic_ai_configuration_has_no_commerce_role():
    connection = ProviderConnection(
        provider_id="test",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.invalid/v1",
    )
    configuration = AIConfiguration.build(
        roles=(AGENT_FAST_ROLE,),
        providers=(connection,),
        bindings=(
            ModelBinding(
                role_id=AGENT_FAST_ROLE.role_id,
                provider_id=connection.provider_id,
                model="test-model",
                capabilities=AGENT_FAST_ROLE.required_capabilities,
            ),
        ),
    )
    assert [profile.profile_id for profile in configuration.profiles.all()] == ["agent.fast"]


def test_agent_tool_loop_completes(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="calc-1", name="calculator", arguments={"expression": "123 * 456"}),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=4, total_tokens=14),
            ),
            ModelResponse(
                text="56088",
                usage=ModelUsage(input_tokens=20, output_tokens=2, total_tokens=22),
            ),
        ]
    )
    store = FileAgentSessionStore(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id)

    result = runtime.start_turn(session.session_id, "Use the calculator for 123 * 456.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "56088"
    assert result.usage.total_tokens == 36
    kinds = [event.kind for event in store.events(session.session_id)]
    assert AgentEventKind.TOOL_REQUESTED in kinds
    assert AgentEventKind.TOOL_COMPLETED in kinds
    assert AgentEventKind.TURN_COMPLETED in kinds


def test_mutating_workspace_tool_requires_approval(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-1",
                        name="write_workspace_note",
                        arguments={"path": "notes/hello.txt", "text": "hello Loom"},
                    ),
                )
            ),
            ModelResponse(text="Done."),
        ]
    )
    store = FileAgentSessionStore(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id)

    waiting = runtime.start_turn(session.session_id, "Write a workspace note.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert waiting.pending_approval is not None
    assert not (tmp_path / "agent_runtime" / "sessions" / session.session_id / "workspace" / "notes" / "hello.txt").exists()

    result = runtime.resume_approval(
        session.session_id,
        waiting.pending_approval.call_id,
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    target = tmp_path / "agent_runtime" / "sessions" / session.session_id / "workspace" / "notes" / "hello.txt"
    assert target.read_text(encoding="utf-8") == "hello Loom"
    kinds = [event.kind for event in store.events(session.session_id)]
    assert AgentEventKind.TOOL_APPROVAL_REQUIRED in kinds
    assert AgentEventKind.TOOL_APPROVED in kinds
