from app.agent_runtime import AgentRuntime
from app.agent_runtime.runtime import AgentRuntime as CoreRuntime

def test_production_event_storage_has_one_owner():
    owners = [cls for cls in AgentRuntime.__mro__ if "_record" in vars(cls)]
    assert owners == [CoreRuntime]
    assert "_on_turn_finished" in vars(CoreRuntime)
    assert "_emit_event" in vars(CoreRuntime)


def test_service_integrations_are_source_owned():
    from app.app_server import LoomAppServerService, LoomRpcController
    from app.app_server_streaming import StreamingLoomAppServerService
    assert LoomAppServerService._load.__module__ == "app.app_server"
    assert LoomAppServerService.thread_resume.__module__ == "app.app_server"
    assert LoomRpcController._initialize.__module__ not in {"app.app_server_recovery_contract", "app.live_steering_stream_contract"}
    assert StreamingLoomAppServerService._on_runtime_event.__module__ == "app.app_server_streaming"


def test_failed_activation_cannot_leave_an_active_turn(tmp_path):
    import pytest
    from app.agent_runtime import FileAgentSessionStore
    class FailingActivation(CoreRuntime):
        def _on_turn_activated(self, session_id, token):
            raise RuntimeError("activation failed")
        def _on_turn_deactivated(self, session_id, token):
            self.released = True
    runtime = FailingActivation(platform=object(), store=FileAgentSessionStore(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="activation failed"):
            runtime._activate("owner")
        assert not runtime._active_tokens
        assert not runtime._turn_lifecycle_tokens
        assert runtime.released
    finally: runtime.close()
