from types import SimpleNamespace
import pytest
from app.ai.profiles import ModelContextLimits
from app.ai.model_context import model_context_limits_from_mapping, model_context_limits_to_camel
from app.ai.model_context import model_context_limits_from_env
from app.agent_runtime.context_limits import resolve_context_limits
import loom_model_bridge as bridge


def resolve(profile):
    runtime = SimpleNamespace(limits=SimpleNamespace(context_window_tokens=None, output_reserve_tokens=None),
        platform=SimpleNamespace(registry=SimpleNamespace(get=lambda _: SimpleNamespace(context_limits=profile))))
    return resolve_context_limits(runtime, SimpleNamespace(profile_id="test", session_id="session"))


def test_work_budget_is_distinct_from_provider_window():
    limits = resolve(ModelContextLimits(context_window_tokens=512_000, working_context_tokens=128_000))
    assert limits.context_window_tokens == 512_000
    assert limits.input_budget_tokens == 482_304
    assert limits.working_context_tokens == 128_000
    assert limits.working_input_budget_tokens == 123_904


def test_builtin_minimax_declares_only_guaranteed_direct_window(monkeypatch):
    monkeypatch.delenv("LOOM_MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    profile = bridge._safe_minimax("MiniMax-M3", {})
    assert profile["contextLimits"]["contextWindowTokens"] == 512_000
    assert profile["contextLimits"]["workingContextTokens"] == 128_000


def test_unknown_model_does_not_acquire_a_default_work_budget():
    limits = resolve(ModelContextLimits())
    assert not limits.window_known
    assert limits.working_context_tokens is None
    assert limits.working_input_budget_tokens is None


def test_working_context_roundtrip_mapping_and_env(monkeypatch):
    model = model_context_limits_from_mapping({"workingContextTokens": 90_000})
    assert model.context_window_tokens is None
    assert model.working_context_tokens == 90_000
    assert model_context_limits_to_camel(model)["workingContextTokens"] == 90_000
    assert model_context_limits_from_env({"LOOM_MODEL_WORKING_CONTEXT_TOKENS": "100000"}).working_context_tokens == 100_000
    monkeypatch.setenv("LOOM_WORKING_CONTEXT_TOKENS", "110000")
    resolved = resolve(model)
    assert not resolved.window_known
    assert resolved.working_context_tokens == 110_000
    assert resolved.working_context_source == "runtime_env"


def test_working_budget_cannot_exceed_declared_hard_window():
    resolved = resolve(ModelContextLimits(context_window_tokens=32_000, working_context_tokens=128_000))
    assert resolved.working_context_tokens == 30_400
    assert resolved.working_input_budget_tokens == resolved.input_budget_tokens


def test_working_budget_must_leave_output_headroom():
    with pytest.raises(ValueError, match="output reserve"):
        resolve(ModelContextLimits(working_context_tokens=2000))


def test_official_metadata_never_leaks_to_proxy_or_unknown_model():
    proxy = {"LOOM_MINIMAX_BASE_URL": "https://proxy.test/v1"}
    assert "contextLimits" not in bridge._safe_minimax("MiniMax-M3", proxy)
    assert "contextLimits" not in bridge._safe_minimax("MiniMax-M_future", {})
    assert "contextLimits" not in bridge._safe_deepseek("deepseek-future", {})
    assert "contextLimits" not in bridge._safe_deepseek("deepseek-flash", {"DEEPSEEK_BASE_URL": "https://proxy.test/v1"})


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_exact_official_deepseek_ids_have_declared_capacity(model):
    profile = bridge._safe_deepseek(model, {})
    assert profile["contextLimits"]["contextWindowTokens"] == 1_048_576
    assert profile["contextLimits"]["workingContextTokens"] == 128_000


def test_discovery_is_keyed_by_route_and_overrides_only_its_route(monkeypatch):
    monkeypatch.setattr(bridge, "_DISCOVERED_CONTEXT_LIMITS", {
        (bridge.MINIMAX_BASE_URL, "minimax-m3"): ModelContextLimits(context_window_tokens=1_000_000),
        (bridge.MANAGED_RELAY_BASE_URL, "minimax-m3"): ModelContextLimits(context_window_tokens=64_000),
    })
    official = bridge._with_discovered_limits(bridge._safe_minimax("MiniMax-M3", {}))
    managed = bridge._with_discovered_limits(bridge._safe_managed("MiniMax-M3", {}))
    assert official["contextLimits"]["contextWindowTokens"] == 1_000_000
    assert official["contextLimits"]["workingContextTokens"] == 128_000
    assert managed["contextLimits"]["contextWindowTokens"] == 64_000
    assert "workingContextTokens" not in managed["contextLimits"]


def test_unknown_opencode_model_has_no_invented_limits(monkeypatch):
    monkeypatch.setattr(bridge, "_DISCOVERED_CONTEXT_LIMITS", {})
    assert "contextLimits" not in bridge._safe_opencode_go("future-undeclared-model", configured=True)


def test_model_store_preserves_working_budget(tmp_path):
    from app.ai.model_store import ModelConfigStore
    store = ModelConfigStore(tmp_path, secret_getter=lambda _: None,
        secret_setter=lambda *_: None, secret_deleter=lambda _: None)
    profile = store.save_model(display_name="work", adapter="openai-compatible", base_url="https://example.test/v1",
        model="unknown", api_key="temporary-test-value", context_limits=ModelContextLimits(working_context_tokens=80_000))
    reloaded = ModelConfigStore(tmp_path, secret_getter=lambda _: None,
        secret_setter=lambda *_: None, secret_deleter=lambda _: None)
    assert reloaded.get(profile.model_id).context_limits.working_context_tokens == 80_000
    assert reloaded.get(profile.model_id).context_limits.context_window_tokens is None


def context_runtime(profile, *, responses=(), prefix="runtime state", events=()):
    from test_context_runtime_v2 import FakeRuntime, Limits
    runtime = FakeRuntime(limits=Limits(context_window_tokens=None, output_reserve_tokens=None, max_messages=0),
        context_limits=profile, responses=responses, events=events)
    runtime._request_stable_contracts = lambda: ()
    runtime._model_system_prompt = lambda _session, _step: prefix
    return runtime


def test_work_budget_triggers_real_compaction_below_provider_hard_window():
    from app.ai import ModelResponse
    from app.agent_runtime.context_budget import prepare_context, estimate_tokens
    from test_context_runtime_v2 import Session, Step, Token
    from test_unknown_context_window_semantics import _history_over
    history = _history_over(9000)
    runtime = context_runtime(ModelContextLimits(context_window_tokens=100_000, working_context_tokens=8000,
        output_reserve_tokens=1000), responses=[ModelResponse(text="Verified work summary; continue the requested task.")])
    session = Session(history)
    session.request_context_frames = []
    messages, metadata = prepare_context(runtime, session, Step(), Token())
    assert metadata["auto_compacted"] is True
    assert metadata["working_context_tokens"] == 8000
    assert len(runtime.model_executor.requests) == 1
    summary_request = runtime.model_executor.requests[0][1]
    assert estimate_tokens(summary_request.messages) + 1000 <= 8000
    assert runtime.commits[0]["archived"] == tuple(history)
    assert metadata["effective_input_budget_tokens"] == 94_000
    assert estimate_tokens(messages) < 8000


def test_work_budget_is_soft_when_fixed_context_exceeds_it():
    from app.ai import AIMessage, MessageRole
    from app.agent_runtime.context_budget import prepare_context
    from test_context_runtime_v2 import Session, Step, Token
    runtime = context_runtime(ModelContextLimits(context_window_tokens=100_000, working_context_tokens=8000,
        output_reserve_tokens=1000), prefix="fixed context " * 5000)
    session = Session([AIMessage(role=MessageRole.USER, content="Continue the task.")])
    session.request_context_frames = []
    _, metadata = prepare_context(runtime, session, Step(), Token())
    assert not runtime.model_executor.requests
    assert metadata.get("auto_compacted") is not True
    assert metadata["working_context_attainable"] is False


def test_provider_usage_crosses_work_watermark_without_crossing_hard_limit():
    from app.ai import ModelResponse
    from app.agent_runtime.context_budget import prepare_context
    from test_context_runtime_v2 import Session, Step, Token, _event
    from test_unknown_context_window_semantics import _history_over
    runtime = context_runtime(ModelContextLimits(context_window_tokens=100_000, working_context_tokens=8000,
        output_reserve_tokens=1000), responses=[ModelResponse(text="Summary of completed checks and current task.")],
        events=[_event("model_response", total_tokens=9000)])
    session = Session(_history_over(3000))
    session.request_context_frames = []
    _, metadata = prepare_context(runtime, session, Step(), Token())
    assert metadata["auto_compacted"] is True
    assert metadata["token_accounting_source"] == "provider_usage"
    assert metadata["effective_input_budget_tokens"] == 94_000


def test_unknown_window_can_opt_in_to_working_policy_without_claiming_capacity():
    from app.ai import ModelResponse
    from app.agent_runtime.context_budget import prepare_context
    from test_context_runtime_v2 import Session, Step, Token
    from test_unknown_context_window_semantics import _history_over
    runtime = context_runtime(ModelContextLimits(working_context_tokens=8000, output_reserve_tokens=1000),
        responses=[ModelResponse(text="Summary preserving the user task and completed checks.")])
    session = Session(_history_over(9000))
    session.request_context_frames = []
    _, metadata = prepare_context(runtime, session, Step(), Token())
    assert metadata["auto_compacted"] is True
    assert metadata["context_budget_source"] == "unbounded"
    assert metadata["effective_input_budget_tokens"] is None


def test_indivisible_summary_input_can_exceed_soft_budget_without_error():
    from app.ai import AIMessage, MessageRole, ModelResponse
    from app.agent_runtime.context_budget import prepare_context, estimate_tokens
    from test_context_runtime_v2 import Session, Step, Token
    runtime = context_runtime(ModelContextLimits(context_window_tokens=100_000, working_context_tokens=8000,
        output_reserve_tokens=1000), responses=[ModelResponse(text="Summary of the large user specification.")])
    history = [AIMessage(role=MessageRole.USER, content="user specification " * 2500)]
    session = Session(history)
    session.request_context_frames = []
    _, metadata = prepare_context(runtime, session, Step(), Token())
    assert metadata["auto_compacted"] is True
    request = runtime.model_executor.requests[0][1]
    assert estimate_tokens(request.messages) + 1000 > 8000
    assert estimate_tokens(request.messages) + 1000 < 95_000
    assert runtime.commits[0]["archived"] == tuple(history)


def test_no_work_budget_preserves_unknown_model_no_compaction():
    from app.agent_runtime.context_budget import prepare_context
    from test_context_runtime_v2 import Session, Step, Token
    from test_unknown_context_window_semantics import _history_over
    runtime = context_runtime(ModelContextLimits())
    session = Session(_history_over(13_000))
    session.request_context_frames = []
    _, metadata = prepare_context(runtime, session, Step(), Token())
    assert metadata["working_context_tokens"] is None
    assert metadata.get("auto_compacted") is not True
    assert not runtime.model_executor.requests
