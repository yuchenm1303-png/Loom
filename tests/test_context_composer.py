"""Exercise the messages actually sent by the production runtime."""
from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.agent_runtime.context_budget import estimate_tokens
from app.ai import AIMessage, ImagePart, MessageRole, ModelResponse, TextPart, ToolCall


class Platform:
    def __init__(self):
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(tool_calls=(ToolCall("list", "list_workspace_files", {}),))
        return ModelResponse(text="Done")


def test_actual_production_prefix_and_budget(tmp_path):
    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
        runtime.start_turn(session.session_id, "Inspect the workspace")
        first, second = platform.requests
        assert "LOOM_LIVE_STEERING_CONTRACT" in first.messages[0].content
        assert second.messages[:len(first.messages)] == first.messages
        runtime.start_turn(session.session_id, "Continue inspecting")
        third = platform.requests[-1]
        assert third.messages[:len(second.messages)] == second.messages
        requested = [e for e in runtime.store.events(session.session_id) if e.kind.value == "model_requested"]
        for request, event in zip(platform.requests, requested):
            assert event.data["estimated_input_tokens_after"] == estimate_tokens(request.messages, request.tools)
            assert event.data["request_layout"]["composer"] == "context_composer_v1"
    finally:
        runtime.close()


def test_single_request_owner_and_raw_history(tmp_path):
    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        owners = [cls for cls in type(runtime).__mro__ if "_prepare_model_request" in vars(cls)]
        assert len(owners) == 1
        assert owners[0].__module__ == "app.agent_runtime.runtime"
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
        runtime.start_turn(session.session_id, "Inspect the workspace")
        stored = runtime.store.load(session.session_id)
        assert all(not m.name.startswith("loom_") for m in stored.messages)
        assert stored.messages[-1].content == "Done"
        assert stored.request_context_frames
        assert all(m.role is not MessageRole.SYSTEM for m in platform.requests[-1].messages[1:])
    finally:
        runtime.close()


def test_context_frames_survive_restart_without_ephemeral_observations(tmp_path):
    from app.agent_runtime.context_composer import capture_context, render_request, stable_prefix, compact_frames
    runtime = AgentRuntime(platform=Platform(), store=FileAgentSessionStore(tmp_path / "state"),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        session.messages.append(AIMessage(role=MessageRole.USER, content="Real user request"))
        text = AIMessage(role=MessageRole.USER, name="loom_tool_observation_text", content="DOM_PRIVATE_MARKER")
        image = AIMessage(role=MessageRole.USER, name="loom_tool_observation", content=(
            TextPart("External visual"), ImagePart("data:image/png;base64,PRIVATE_IMAGE")))
        runtime._collect_model_observations = lambda session, step: ([text, image], {})
        step = runtime._build_step_context(session, next_model_step=False)
        capture_context(runtime, session, step)
        messages = render_request(runtime, session, stable_prefix(runtime, session, step), session.messages)
        assert text in messages and image in messages
        capture_context(runtime, session, step)
        assert messages == render_request(runtime, session, stable_prefix(runtime, session, step), session.messages)
        serialized = (runtime.store.session_dir(session.session_id) / "session.json").read_text(encoding="utf-8")
        assert "DOM_PRIVATE_MARKER" not in serialized
        assert "PRIVATE_IMAGE" not in serialized
        compact_frames(runtime, session, len(session.messages))
        compacted = render_request(runtime, session, stable_prefix(runtime, session, step), session.messages)
        assert text in compacted and image in compacted
        runtime.store.save(session)
        runtime._request_ephemeral_frames.clear()
        restored = runtime.store.load(session.session_id)
        cold = render_request(runtime, restored, stable_prefix(runtime, restored, step), restored.messages)
        assert any(m.name == "loom_runtime_state" for m in cold)
        assert any("unavailable after Host restart" in str(m.content) for m in cold)
        assert "DOM_PRIVATE_MARKER" not in str(cold)
        assert "PRIVATE_IMAGE" not in str(cold)
        assert restored.messages == session.messages
    finally:
        runtime.close()


def test_recovery_transport_is_budgeted_by_the_same_composer(tmp_path):
    class RecoveryPlatform:
        def __init__(self): self.requests = []
        def execute_chat(self, profile, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(text="partial response", finish_reason="length")
            return ModelResponse(text="Done", finish_reason="stop")
    platform = RecoveryPlatform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        result = runtime.start_turn(session.session_id, "Finish the task")
        assert result.status.value == "completed"
        events = [e for e in runtime.store.events(session.session_id) if e.kind.value == "model_requested"]
        assert len(platform.requests) == 2
        for request, event in zip(platform.requests, events):
            assert event.data["estimated_input_tokens_after"] == estimate_tokens(request.messages, request.tools)
            assert event.data["request_layout"]["message_count"] == len(request.messages)
        assert len([m for m in platform.requests[-1].messages if m.role is MessageRole.SYSTEM]) == 1
        recovery = platform.requests[-1].messages[-1]
        assert recovery.name == "loom_terminal_recovery"
        assert recovery.role is MessageRole.USER
        assert "partial response" in recovery.content
        assert not any(m.name == "loom_terminal_recovery" for m in runtime.store.load(session.session_id).messages)
        assert all("partial response" not in str(record["content"])
                   for frame in runtime.store.load(session.session_id).request_context_frames
                   for record in frame["messages"] if record["name"] == "loom_terminal_recovery")
    finally: runtime.close()
