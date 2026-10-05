import pytest

from app.ai.execution_control import ExecutionControl


def test_stream_timeout_is_configured_through_model_binding():
    from app.ai import AGENT_FAST_ROLE, AIConfiguration, ModelBinding, ProviderConnection, ProviderAdapter, CredentialRef
    connection = ProviderConnection(provider_id="test", adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test"), base_url="https://example.invalid/v1")
    binding = ModelBinding(role_id=AGENT_FAST_ROLE.role_id, provider_id="test", model="test",
        capabilities=AGENT_FAST_ROLE.required_capabilities, stream_idle_timeout_seconds=420)
    config = AIConfiguration.build(roles=(AGENT_FAST_ROLE,), providers=(connection,), bindings=(binding,))
    assert config.profiles.get(AGENT_FAST_ROLE.role_id).stream_idle_timeout_seconds == 420


def test_raw_chunks_and_content_have_independent_clocks(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.ai.execution_control.time.monotonic", lambda: clock[0])
    control = ExecutionControl()
    clock[0] = 101
    control.note_chunk()
    assert control.last_chunk_at == 101
    assert control.progress_at == 0
    clock[0] = 102
    control.note_progress(tool_fragment=True)
    clock[0] = 105
    control.note_chunk()
    timing = control.stream_timing()
    assert timing["first_chunk_ms"] == 1000
    assert timing["first_content_ms"] == 2000
    assert timing["first_tool_fragment_ms"] == 2000
    assert timing["chunk_count"] == 2
    assert timing["content_chunk_count"] == 1
    assert timing["tool_argument_fragments"] == 1
    assert timing["max_chunk_gap_ms"] == 4000
    assert timing["max_content_gap_ms"] == 3000


def test_activity_notification_has_ui_consumers():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / "app/app_server_streaming.py").read_text(encoding="utf-8")
    state = (root / "desktop-react/src/state/useLoomCore.ts").read_text(encoding="utf-8")
    ui = (root / "desktop-react/src/components/RunProgress.tsx").read_text(encoding="utf-8")
    assert '"turn/modelActivity"' in server
    assert '"turn/modelActivity"' in state
    assert "contentGapSeconds" in ui


def test_stall_retry_changes_guidance_after_one_replay(tmp_path, monkeypatch):
    from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.agent_runtime.model_execution import ModelRequestTimeout
    from app.ai import ModelResponse
    monkeypatch.setattr("app.agent_runtime.turn_runner.wait_for_signal", lambda *args: None)

    class Platform:
        requests = []

        def execute_chat(self, profile, request):
            self.requests.append(request)
            if len(self.requests) < 3:
                raise ModelRequestTimeout("stalled", reason="stream_stall_timeout", retryable=True)
            return ModelResponse(text="Done")

    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Write").status is AgentStatus.COMPLETED
        assert not any(m.name == "loom_terminal_recovery" for m in platform.requests[1].messages)
        recovery = next(m for m in platform.requests[2].messages if m.name == "loom_terminal_recovery")
        assert "stalled" in recovery.content
        assert "smaller writes" in recovery.content
        rejected = [e for e in runtime.store.events(session.session_id) if e.kind.value == "model_response_rejected"]
        assert len(rejected) == 2
        assert all("stream_timing" in e.data for e in rejected)
    finally:
        runtime.close()


@pytest.mark.parametrize("fragmented", [False, True])
def test_buffered_or_fragmented_twenty_k_write_executes_once(tmp_path, fragmented):
    import json
    import time
    from types import SimpleNamespace as NS
    from test_provider_streaming import _streaming_backend
    from app.ai.streaming_platform import StreamingAIPlatform
    from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.agent_runtime.model_execution import ModelExecutor
    from app.agent_runtime.workspace_tools import loom_default_tools

    def chunk(tool=None, text=None, finish=None):
        return NS(id="sample", usage=None, choices=[NS(finish_reason=finish,
            delta=NS(content=text, reasoning_content=None, tool_calls=[tool] if tool else None))])

    payload = "x" * 20_000
    args = json.dumps({"path": "report.txt", "text": payload})

    class Client:
        count = 0

        def create(self, **kwargs):
            self.count += 1

            def stream():
                if self.count > 1:
                    yield chunk(text="Done")
                    yield chunk(finish="stop")
                    return
                yield chunk(text="Writing")
                parts = [args[i:i + 1000] for i in range(0, len(args), 1000)] if fragmented else [args]
                for index, part in enumerate(parts):
                    time.sleep(0.02 if fragmented else 0.1)
                    yield chunk(tool=NS(index=0, id="write" if index == 0 else None,
                        function=NS(name="write_workspace_text" if index == 0 else None, arguments=part)))
                yield chunk(finish="tool_calls")
            return stream()

    client = Client()
    backend = _streaming_backend(client)
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(backend.profile, backend)
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    runtime.model_executor = ModelExecutor(timeout=2, stall_timeout=1, max_duration=10)
    live_events = []
    runtime.subscribe_stream(live_events.append)
    try:
        session = runtime.create_session(backend.profile.profile_id, workspace_dir=tmp_path, permission_mode="full-access")
        result = runtime.start_turn(session.session_id, "Write report")
        assert result.status is AgentStatus.COMPLETED
        assert (tmp_path / "report.txt").read_text() == payload
        events = runtime.store.events(session.session_id)
        assert any(event.kind.value == "model_activity" for event in live_events)
        assert not any(event.kind.value == "model_activity" for event in events)
        executed = [e for e in events if e.kind.value == "tool_completed"]
        assert len(executed) == 1
        response = next(e for e in events if e.kind.value == "model_response")
        timing = response.data["stream_timing"]
        assert timing["first_tool_fragment_ms"] is not None
        assert timing["tool_argument_fragments"] == ((len(args) + 999) // 1000 if fragmented else 1)
    finally:
        runtime.close()
