import pytest
from app.agent_runtime import AgentEvent, AgentEventKind
from app.app_server import _event_item_identity, _apply_event_to_item


def event(kind, data):
    return AgentEvent(event_id="retry-event", session_id="s", turn_id="t", kind=kind, created_at="now", data=data)


@pytest.mark.parametrize("kind,attempt", [(AgentEventKind.MODEL_RESPONSE_REJECTED, 1), (AgentEventKind.MODEL_TRANSPORT_RETRY, 2)])
def test_retry_notice_has_stable_identity_count_and_expandable_reason(kind, attempt):
    retry = event(kind, {"will_retry": True, "attempt": attempt, "retry_limit": 5, "error": "connection reset"})
    identity = _event_item_identity(retry)
    assert identity == ("model-retry:retry-event", "model_retry")
    item = {"type": identity[1]}
    _apply_event_to_item(item, retry)
    assert item["text"] == "正在重新连接…"
    assert item["content"] == "connection reset"


def test_permanent_or_exhausted_failure_never_claims_to_retry():
    assert _event_item_identity(event(AgentEventKind.MODEL_RESPONSE_REJECTED, {"will_retry": False})) is None


def test_transport_retries_continue_beyond_previous_limit_until_recovered(tmp_path, monkeypatch):
    from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.ai.errors import AITransportError
    waits = []
    monkeypatch.setattr("app.agent_runtime.turn_runner.wait_for_signal", lambda token, revision, seconds: waits.append(seconds))
    class Platform:
        calls = 0
        def execute_chat(self, profile, request):
            self.calls += 1
            if self.calls <= 8:
                raise AITransportError("upstream unavailable", retryable=True, status_code=429)
            from app.ai import ModelResponse
            return ModelResponse(text="Done")
    platform = Platform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        result = runtime.start_turn(session.session_id, "Check")
        assert result.status is AgentStatus.COMPLETED
        assert platform.calls == 9
        assert waits == [1, 2, 4, 8, 16, 20, 20, 20]
        retries = [e for e in store.events(session.session_id) if e.kind is AgentEventKind.MODEL_RESPONSE_REJECTED]
        assert [e.data["will_retry"] for e in retries] == [True] * 8
        assert not any(e.kind is AgentEventKind.TOOL_STARTED for e in store.events(session.session_id))
    finally:
        runtime.close()
