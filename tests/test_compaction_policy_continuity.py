"""Compaction policies must preserve history until the real request bound bites."""
from app.ai import AIMessage, MessageRole, ModelContextLimits, ModelResponse, ToolCall
from app.agent_runtime.context_budget import prepare_context
from test_context_runtime_v2 import Session, Step, Token
from test_working_context_limits import context_runtime, resolve


def test_configured_auto_threshold_is_capped_at_ninety_percent_of_model_window():
    limits = resolve(ModelContextLimits(context_window_tokens=512_000,
        auto_compact_token_limit=510_000))
    assert limits.auto_compact_token_limit == 460_800


def test_default_large_window_does_not_compact_at_former_builtin_work_target():
    from test_unknown_context_window_semantics import _history_over
    runtime = context_runtime(ModelContextLimits(context_window_tokens=512_000))
    session = Session(_history_over(150_000))
    _, metadata = prepare_context(runtime, session, Step(), Token())
    assert not runtime.model_executor.requests
    assert not metadata.get("auto_compacted")
    assert metadata["context_limits"]["auto_compact_token_limit"] == 460_800


def test_invalid_summary_retry_keeps_the_same_evidence_input():
    history = [AIMessage(role=MessageRole.USER, content="Retain original evidence: FIRST_PROOF"),
        AIMessage(role=MessageRole.ASSISTANT, content="Inspection completed."),
        AIMessage(role=MessageRole.USER, content="Continue the same task.")]
    runtime = context_runtime(ModelContextLimits(context_window_tokens=100_000,
        auto_compact_token_limit=1), responses=[
            ModelResponse(tool_calls=(ToolCall("bad-summary", "exec", {}),)),
            ModelResponse(text="The first inspection completed; continue the task.")])
    _, metadata = prepare_context(runtime, Session(history), Step(), Token())
    first, second = [request.messages for _, request in runtime.model_executor.requests]
    assert first == second
    assert history[0] in second
    assert metadata["compaction_trimmed_messages"] == 0


def test_unknown_capacity_does_not_use_work_target_as_summary_input_ceiling():
    from test_unknown_context_window_semantics import _history_over
    history = _history_over(12_000)
    runtime = context_runtime(ModelContextLimits(working_context_tokens=8000,
        output_reserve_tokens=1000), responses=[ModelResponse(text="Keep prior facts and continue.")])
    _, metadata = prepare_context(runtime, Session(history), Step(), Token())
    request = runtime.model_executor.requests[0][1]
    assert all(item in request.messages for item in history)
    assert metadata["summary_request_ceiling_tokens"] is None
    assert metadata["compaction_trimmed_messages"] == 0


def test_production_checkpoint_does_not_enforce_a_fallback_model_capacity(tmp_path, monkeypatch):
    from test_context_runtime import _runtime
    from app.agent_runtime import context_limits
    monkeypatch.setattr(context_limits, "_UNKNOWN_WINDOW_SANITY_CEILING", 16_000)
    runtime, store = _runtime(tmp_path)
    session = runtime.create_session("test", workspace_dir=tmp_path)
    session.messages = [AIMessage(role=MessageRole.USER, content="Keep the original objective.")]
    store.save(session)
    try:
        # No model metadata exists. This caller-supplied checkpoint is larger
        # than the fallback meter, but no provider capacity has been established.
        checkpoint = runtime.compact_context(session.session_id, "evidence " * 3000)
        restored = store.load(session.session_id)
        assert checkpoint.retained_message_count == 1
        assert restored.messages[0] == session.messages[0]
        assert len(runtime.list_context_checkpoints(session.session_id)) == 1
    finally:
        runtime.close()


def test_production_checkpoint_uses_provider_calibration_for_the_capacity_gate(tmp_path):
    from types import SimpleNamespace
    from test_context_runtime import _runtime
    from app.agent_runtime import AgentEventKind
    runtime, store = _runtime(tmp_path)
    runtime.platform.registry = SimpleNamespace(get=lambda _: SimpleNamespace(
        context_limits=ModelContextLimits(context_window_tokens=32_000)))
    session = runtime.create_session("test", workspace_dir=tmp_path)
    session.messages = [AIMessage(role=MessageRole.USER, content="Original objective"),
        AIMessage(role=MessageRole.USER, content="Latest correction")]
    store.save(session)
    try:
        for _ in range(3):
            runtime._record(session, AgentEventKind.MODEL_REQUESTED,
                data={"estimated_input_tokens_after": 10_000})
            runtime._record(session, AgentEventKind.MODEL_RESPONSE,
                data={"usage": {"input_tokens": 7000}})
        checkpoint = runtime.compact_context(session.session_id, "evidence " * 6500)
        record = [event.data["context_after_compaction"] for event in store.events(session.session_id)
            if event.kind is AgentEventKind.CONTEXT_CHECKPOINTED][-1]
        assert record["estimated_input_tokens_after"] > record["resolved_input_budget_tokens"]
        assert record["calibrated_input_tokens_after"] <= record["resolved_input_budget_tokens"]
        assert checkpoint.retained_message_count == 2
        assert store.load(session.session_id).messages[:2] == session.messages
    finally:
        runtime.close()


def test_optional_reference_cannot_evict_latest_user_instruction():
    from app.agent_runtime import context_compaction
    from app.agent_runtime.continuity import (
        COMPACTION_REFERENCE_MESSAGE_NAME, _fit_reference_without_breaking_budget,
    )
    from test_context_runtime_v2 import Envelope
    runtime = context_runtime(ModelContextLimits(context_window_tokens=8000,
        output_reserve_tokens=1000))
    latest = AIMessage(role=MessageRole.USER, content="Latest correction " * 200)
    session = Session([latest])
    replacement = (latest, AIMessage(role=MessageRole.USER,
        name=context_compaction.COMPACTION_MESSAGE_NAME, content="Task summary"))
    reference = AIMessage(role=MessageRole.USER, name=COMPACTION_REFERENCE_MESSAGE_NAME,
        content="metadata " * 1800)
    result, injected = _fit_reference_without_breaking_budget(runtime, session, Step(),
        Envelope(), "en", replacement, reference, context_compaction)
    assert result == replacement
    assert not injected


def test_model_switch_does_not_reuse_previous_provider_token_accounting():
    from types import SimpleNamespace
    from app.agent_runtime.context_budget import (
        _estimator_calibration, _latest_provider_context_tokens, _observed_context_ceiling,
    )
    events = []
    for _ in range(3):
        identity = {"profile_id": "agent.fast", "provider": "shared-provider-id", "model": "old-model"}
        events += [SimpleNamespace(kind=SimpleNamespace(value="model_requested"),
            data={**identity, "estimated_input_tokens_after": 100_000}),
            SimpleNamespace(kind=SimpleNamespace(value="model_response"),
            data={**identity, "usage": {"input_tokens": 70_000, "total_tokens": 70_100}})]
    events.append(SimpleNamespace(kind=SimpleNamespace(value="model_response_rejected"),
        data={**identity, "reason": "context_window_exceeded", "rejected_input_tokens": 160_000}))
    runtime = context_runtime(ModelContextLimits(), events=events)
    current = SimpleNamespace(provider="shared-provider-id", model="new-model", context_limits=ModelContextLimits())
    runtime.platform.registry.get = lambda _: current
    session = Session([])
    assert _estimator_calibration(runtime, session) == (1.0, 0)
    assert _latest_provider_context_tokens(runtime, session) is None
    assert _observed_context_ceiling(runtime, session) is None
    # Switching back can use its own durable measurements again.
    current.model = "old-model"
    assert _estimator_calibration(runtime, session) == (0.7, 3)
    assert _latest_provider_context_tokens(runtime, session) == 70_100
    assert _observed_context_ceiling(runtime, session) == 160_000
    events.append(SimpleNamespace(kind=SimpleNamespace(value="model_response"),
        data={**identity, "model": "new-model", "usage": {"total_tokens": 9000}}))
    runtime.store._events = tuple(events)
    # The foreign response advanced history. Old matching usage is no longer
    # the active-window clock, even when this model was used earlier.
    assert _latest_provider_context_tokens(runtime, session) is None
    current.model = "new-model"
    assert _latest_provider_context_tokens(runtime, session) == 9000
