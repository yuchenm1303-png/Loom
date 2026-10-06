from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.contracts import AgentEventKind
from app.ai import AIMessage, MessageRole, ModelResponse


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
