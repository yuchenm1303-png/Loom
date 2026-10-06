from pathlib import Path
import inspect

def test_production_runtime_has_no_hidden_completion_review_entrypoint():
    from app.agent_runtime import CoreAgentRuntime
    assert "stop_hook" not in inspect.signature(CoreAgentRuntime).parameters
    assert not (Path(__file__).parents[1] / "app/agent_runtime/turn_stop.py").exists()
    assert not (Path(__file__).parents[1] / "app/agent_runtime/turn_continuation.py").exists()

def test_removed_review_request_purpose_cannot_reenter_provider_path():
    import pytest
    from app.ai import ChatRequest, AIMessage, MessageRole
    with pytest.raises(ValueError, match="unsupported internal request purpose"):
        ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="Check"),),purpose="stop_review")
