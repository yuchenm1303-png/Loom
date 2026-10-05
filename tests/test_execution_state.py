import pytest
from app.ai import ModelResponse, StreamEvent, StreamEventKind, ToolCall
from app.agent_runtime.execution_state import next_execution_action, ExecutionAction as A
from app.ai.streaming_platform import _StreamAccumulator


@pytest.mark.parametrize("response, flags, action, source", [
    (ModelResponse(text="Then continue", finish_reason="stop"), {}, A.DELIVER, "legacy_finish_compatibility"),
    (ModelResponse(text="Progress", end_turn=False), {}, A.SAMPLE, "provider_end_turn"),
    (ModelResponse(text="Answer", phase="commentary"), {}, A.DELIVER, "legacy_finish_compatibility"),
    (ModelResponse(text="Done", end_turn=True), {"pending_input": True}, A.SAMPLE, "pending_input"),
    (ModelResponse(tool_calls=(ToolCall("c", "echo", {}),), end_turn=True), {}, A.EXECUTE_TOOLS, "tool_calls"),
    (ModelResponse(end_turn=False), {"cancelled": True}, A.CANCEL, "cancellation"),
    (ModelResponse(), {"waiting_approval": True}, A.WAIT_APPROVAL, "approval"),
])
def test_execution_intent_is_independent_of_prose_and_display_phase(response, flags, action, source):
    result = next_execution_action(response, **flags)
    assert (result.action, result.source) == (action, source)


def test_stream_aggregation_preserves_native_intent():
    accumulator = _StreamAccumulator()
    accumulator.consume(StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="Progress"))
    accumulator.consume(StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="completed", phase="commentary", end_turn=False))
    result = accumulator.finalize()
    assert result.phase == "commentary" and result.end_turn is False


def test_native_phase_survives_storage_and_responses_replay():
    from app.ai import AIMessage, MessageRole, ChatRequest
    from app.agent_runtime.storage import _message_to_dict, _message_from_dict
    from app.ai.opencode_go_runtime import _OpenCodeGoResponsesBackend
    message = AIMessage(role=MessageRole.ASSISTANT, content="Progress", phase="commentary")
    restored = _message_from_dict(_message_to_dict(message))
    assert restored.phase == "commentary"
    backend = object.__new__(_OpenCodeGoResponsesBackend)
    assert backend._input(ChatRequest(messages=(restored,)))[0]["phase"] == "commentary"
    assert _message_from_dict({"role": "assistant", "content": "Legacy"}).phase is None


def test_empty_native_followup_is_not_a_reasoning_only_failure():
    accumulator = _StreamAccumulator()
    accumulator.consume(StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="completed", end_turn=False))
    response = accumulator.finalize()
    assert response.text == "" and response.end_turn is False
    assert next_execution_action(response).action is A.SAMPLE


def test_messages_tool_use_is_a_valid_transport_completion_not_a_task_end():
    from app.agent_runtime.turn_response_validation import invalid_terminal_response
    response = ModelResponse(tool_calls=(ToolCall("c", "echo", {}),), finish_reason="tool_use", end_turn=False)
    assert invalid_terminal_response(response) == ""
    assert next_execution_action(response).action is A.EXECUTE_TOOLS
