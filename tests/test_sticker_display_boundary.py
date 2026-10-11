from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.contracts import AgentEventKind
from app.ai import AIMessage, MessageRole, ModelResponse
from app.ai import ToolCall
from app.agent_runtime.workspace_tools import loom_default_tools
from app.agent_runtime.stickers import extract_keys
import pytest


class Platform:
    def __init__(self):
        self.requests = []
    def execute_chat(self, _profile, request):
        self.requests.append(request)
        return ModelResponse(text="The requested work is complete and the result has been verified.")


def test_stickers_decorate_display_without_rewriting_model_history(tmp_path):
    platform = Platform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Please finish the work")
        event = next(e for e in store.events(session.session_id) if e.kind is AgentEventKind.MODEL_RESPONSE)
        original = "The requested work is complete and the result has been verified."
        assert event.data["text"] == original
        assert store.load(session.session_id).messages[-1].content == original
        assert "[[AI_LEDGER_INLINE_STICKER:" in event.data["display_text"]
        assert event.data["display_runtime_authored"] is True
        completed = next(e for e in store.events(session.session_id) if e.kind is AgentEventKind.TURN_COMPLETED)
        assert completed.data["text"] == original
        assert completed.data["display_text"] == event.data["display_text"]
        assert not any(m.name in {"loom_inline_sticker_protocol", "loom_balanced_sticker_distribution"}
                       for m in platform.requests[0].messages)
    finally:
        runtime.close()


def test_legacy_sticker_projection_preserves_prose_and_original_record():
    from app.agent_runtime.streaming_runtime import strip_legacy_sticker_protocol
    original = "Before\n[[AI_LEDGER_INLINE_STICKER:soft_smile]]After\n[[AI_LEDGER_STICKER_PLAN_V1_BEGIN]]{\"candidates\":[]}[[AI_LEDGER_STICKER_PLAN_V1_END]]"
    message = AIMessage(role=MessageRole.ASSISTANT, content=original)
    assert strip_legacy_sticker_protocol(message.content) == "Before\nAfter\n"
    assert message.content == original
    assert strip_legacy_sticker_protocol("AI_LEDGER is ordinary prose") == "AI_LEDGER is ordinary prose"


@pytest.mark.parametrize("style", ["plain", "inline", "think", "empty", "code"])
@pytest.mark.parametrize("streaming", [False, True])
def test_tool_commentary_uses_display_stickers_without_changing_history(tmp_path, style, streaming):
    texts = {
        "plain": "我先检查工作目录中的文件，再根据实际结果选择下一步操作。",
        "inline": "我先检查工作目录中的文件，再继续处理。[[AI_LEDGER_INLINE_STICKER:thinking_soft]]",
        "think": "<think>先确认工作目录状态。</think>我先检查工作目录中的文件，再根据实际结果选择下一步操作。",
        "empty": "",
        "code": "```python\nprint('checking workspace files')\n```",
    }
    commentary = texts[style]
    final = "检查已经完成，工作目录状态已确认，可以继续处理后续任务。"
    call = ToolCall("list", "list_workspace_files", {})
    class ToolPlatform:
        def __init__(self):
            self.responses = iter([ModelResponse(text=commentary, tool_calls=(call,)), ModelResponse(text=final)])
        def execute_chat(self, _profile, _request):
            return next(self.responses)
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=ToolPlatform(), store=store, tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        runtime.set_sticker_preferences({"frequency": 100, "maxPerReply": 4, "repeatCount": 3})
        runtime._provider_streaming_enabled = streaming
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
        runtime.start_turn(session.session_id, "检查工作目录")
        responses = [e for e in store.events(session.session_id) if e.kind is AgentEventKind.MODEL_RESPONSE]
        progress, answer = responses
        assert progress.data["text"] == commentary
        assert progress.data["tool_calls"]
        assert len(extract_keys(progress.data["display_text"])) == (0 if style in {"empty", "code"} else 1)
        messages = store.load(session.session_id).messages
        assert any(m.role is MessageRole.ASSISTANT and m.content == commentary for m in messages)
        assert messages[-1].content == final
        completed = next(e for e in store.events(session.session_id) if e.kind is AgentEventKind.TURN_COMPLETED)
        assert completed.data["display_text"] == answer.data["display_text"]
        assert completed.data["text"] == final
        assert runtime._sticker_diagnostics == {}
    finally:
        runtime.close()


@pytest.mark.parametrize("frequency,user_text", [(0, "继续"), (100, "不要发表情包")])
def test_tool_commentary_respects_opt_out_even_with_streamed_markers(tmp_path, frequency, user_text):
    runtime = AgentRuntime(platform=Platform(), store=FileAgentSessionStore(tmp_path / "state"),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        runtime.set_sticker_preferences({"frequency": frequency})
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        session.messages.append(AIMessage(role=MessageRole.USER, content=user_text))
        runtime._provider_streaming_enabled = True
        text = "我先检查工作目录中的文件，再根据实际结果选择下一步操作。[[AI_LEDGER_INLINE_STICKER:thinking_soft]]"
        key = (session.session_id, session.current_turn_id, "step")
        runtime._sticker_stream_text[key] = text
        progress = runtime._project_event_payload(session, AgentEventKind.MODEL_RESPONSE,
            {"step_id": "step", "text": text, "tool_calls": [{"name": "list_workspace_files"}]})
        assert extract_keys(progress["display_text"]) == []
        assert progress["text"] == text
        assert runtime._sticker_final_by_turn == {}
    finally:
        runtime.close()


def test_progress_frequency_is_stable_bounded_and_provider_independent(tmp_path):
    runtime = AgentRuntime(platform=Platform(), store=FileAgentSessionStore(tmp_path / "state"),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.set_sticker_preferences({"frequency": 50, "maxPerReply": 4, "repeatCount": 3})
        choose = lambda step: runtime._sticker_response_preferences(session, step, is_final=False)
        choices = [choose(str(step)) for step in range(100)]
        assert 25 < sum(p.frequency > 0 for p in choices) < 75
        assert choices == [choose(str(step)) for step in range(100)]
        assert all(p.max_per_reply == 1 and p.repeat_count == 1 for p in choices)
        runtime.set_sticker_preferences({"frequency": 0})
        assert all(choose(str(step)).frequency == 0 for step in range(10))
    finally:
        runtime.close()
