from types import SimpleNamespace as NS

import pytest

from test_provider_streaming import _streaming_backend
from app.ai.errors import AITransportError


@pytest.mark.parametrize("body", [
    {"error": {"code": "insufficient_quota"}},
    {"error": {"type": "rate_limit_error", "message": "已达到 Token Plan 用量上限 (2056)", "http_code": "429"}},
])
def test_quota_exhaustion_does_not_repeat_http_request(body):
    class Client:
        count = 0

        def create(self, **kwargs):
            self.count += 1
            error = RuntimeError("quota")
            error.status_code = 429
            error.body = body
            raise error

    client = Client()
    backend = _streaming_backend(client)
    with pytest.raises(AITransportError) as caught:
        backend._create({})
    assert client.count == 1
    assert type(caught.value).__name__ == "AIQuotaExceeded"
    assert not caught.value.retryable
    assert caught.value.status_code == 429


def test_throttling_is_returned_to_runner_with_retry_after():
    class Client:
        count = 0

        def create(self, **kwargs):
            self.count += 1
            error = RuntimeError("rate limited")
            error.status_code = 429
            error.body = {"error": {"type": "rate_limit_error"}}
            error.response = NS(headers={"Retry-After": "2"})
            raise error

    client = Client()
    with pytest.raises(AITransportError) as caught:
        _streaming_backend(client)._create({})
    assert client.count == 1
    assert caught.value.retryable
    assert caught.value.retry_after_seconds == 2


def test_runner_records_throttle_and_honors_retry_after(tmp_path, monkeypatch):
    from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.ai import ModelResponse
    waits = []
    monkeypatch.setattr("app.agent_runtime.turn_runner.wait_for_signal",
                        lambda token, revision, seconds: waits.append(seconds))

    class Platform:
        calls = 0

        def execute_chat(self, profile, request):
            self.calls += 1
            if self.calls == 1:
                raise AITransportError("throttled", status_code=429, retry_after_seconds=2)
            return ModelResponse(text="Done")

    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Check").status is AgentStatus.COMPLETED
        assert platform.calls == 2
        assert waits == [2]
        rejected = [e for e in runtime.store.events(session.session_id) if e.kind.value == "model_response_rejected"]
        assert len(rejected) == 1
        assert rejected[0].data["provider_status_code"] == 429
        assert rejected[0].data["will_retry"] is True
    finally:
        runtime.close()
