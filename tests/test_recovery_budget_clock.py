"""Long failed samples must not consume recovery time before recovery starts."""
import pytest

from app.ai import ModelResponse
from app.ai.errors import AITransportError
from app.agent_runtime import (AgentLimits, AgentRuntime, AgentStatus,
    FileAgentSessionStore, SandboxManager, SandboxPolicy)
from app.agent_runtime.model_execution import ModelRequestTimeout


@pytest.mark.parametrize("failure", ["first_output_timeout", "stream_stall_timeout", "transport"])
def test_long_failed_request_still_gets_recovery(tmp_path, monkeypatch, failure):
    clock = [100.0]
    monkeypatch.setattr("app.agent_runtime.turn_runner.time.perf_counter", lambda: clock[0])
    waits = []
    monkeypatch.setattr("app.agent_runtime.turn_runner.wait_for_signal",
        lambda token, revision, delay: waits.append(delay))

    class Platform:
        calls = 0
        def execute_chat(self, profile, request):
            self.calls += 1
            if self.calls == 1:
                clock[0] += 340
                if failure == "transport":
                    from app.ai.execution_control import note_progress
                    note_progress()
                    raise AITransportError("connection lost", retryable=True)
                raise ModelRequestTimeout("provider stalled", reason=failure, retryable=True)
            return ModelResponse(text="Recovered")

    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF), limits=AgentLimits())
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        result = runtime.start_turn(session.session_id, "Continue")
        assert result.status is AgentStatus.COMPLETED
        assert platform.calls == 2
        assert waits == [1]
        failures = [e.data for e in runtime.store.events(session.session_id)
                    if e.kind.value == "model_response_rejected"]
        assert failures[0]["will_retry"] is True
        assert failures[0]["recovery_elapsed_ms"] == 0
        requests = [e.data for e in runtime.store.events(session.session_id)
                    if e.kind.value == "model_requested"]
        assert requests[0]["step_id"] != requests[1]["step_id"]
    finally:
        runtime.close()


def test_recovery_budget_still_bounds_repeated_slow_failures(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.agent_runtime.turn_runner.time.perf_counter", lambda: clock[0])
    monkeypatch.setattr("app.agent_runtime.turn_runner.wait_for_signal", lambda *args: None)

    class Platform:
        calls = 0
        def execute_chat(self, profile, request):
            self.calls += 1
            clock[0] += 100
            raise ModelRequestTimeout("stalled", reason="stream_stall_timeout", retryable=True)

    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF), limits=AgentLimits())
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Continue").status is AgentStatus.FAILED
        assert platform.calls == 2
        failures = [e.data for e in runtime.store.events(session.session_id)
                    if e.kind.value == "model_response_rejected"]
        assert [e["will_retry"] for e in failures] == [True, False]
        assert failures[-1]["recovery_elapsed_ms"] >= 100_000
    finally:
        runtime.close()
