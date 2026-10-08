"""Cached input accounting survives provider parsing, rejected samples and storage."""
from types import SimpleNamespace as NS

import pytest

from app.ai import ModelResponse, ModelUsage
from app.ai.openai_runtime import _usage_from
from app.ai.opencode_go_runtime import _usage
from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.storage import _usage_from_dict, _usage_to_dict


@pytest.mark.parametrize("detail_name", ["prompt_tokens_details", "input_tokens_details"])
def test_openai_usage_reads_cached_details_without_discounting_input(detail_name):
    response = NS(usage=NS(prompt_tokens=100, completion_tokens=7, total_tokens=107,
                          **{detail_name: NS(cached_tokens=80)}))
    usage = _usage_from(response)
    assert usage.cached_input_tokens == 80
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (100, 7, 107)


def test_legacy_usage_storage_defaults_to_zero_and_roundtrips_cache():
    assert _usage_from_dict({"input_tokens": 10}).cached_input_tokens == 0
    usage = ModelUsage(100, 7, 107, cached_input_tokens=80)
    assert _usage_from_dict(_usage_to_dict(usage)) == usage
    assert ModelUsage(1, 2, 3).cached_input_tokens == 0


def test_opencode_anthropic_counts_cache_tokens_as_part_of_input():
    usage = _usage(10, 7, cached_input_tokens=80, cache_creation_input_tokens=20)
    assert usage.cached_input_tokens == 80
    assert (usage.input_tokens, usage.total_tokens) == (110, 117)


def test_fake_provider_cached_usage_survives_production_event_and_restore(tmp_path):
    class Platform:
        def execute_chat(self, _profile, request):
            return ModelResponse(text="Done", usage=ModelUsage(100, 7, 107, cached_input_tokens=80))

    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=Platform(), store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        runtime.set_sticker_preferences({"frequency": 0})
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Finish")
        restored = store.load(session.session_id)
        assert restored.usage.cached_input_tokens == 80
        observed = [e.data["usage"] for e in store.events(session.session_id)
                    if e.kind.value == "model_response"]
        assert len(observed) == 1
        assert observed[0]["cached_input_tokens"] == 80
        assert observed[0]["total_tokens"] == 107
    finally:
        runtime.close()


@pytest.mark.parametrize("module_name", ["runtime", "context_runtime", "memory_runtime"])
def test_all_usage_accumulators_preserve_cached_subset(module_name):
    import importlib
    add = importlib.import_module("app.agent_runtime." + module_name)._add_usage
    result = add(ModelUsage(100, 7, 107, 80), ModelUsage(20, 2, 22, 10))
    assert result == ModelUsage(120, 9, 129, 90)


def test_responses_usage_does_not_double_count_cached_input():
    from app.ai.opencode_go_runtime import _responses_usage
    assert _responses_usage(NS(input_tokens=100, output_tokens=7, total_tokens=107,
                               input_tokens_details=NS(cached_tokens=80))) == ModelUsage(100, 7, 107, 80)


def test_empty_stream_error_preserves_cached_subset():
    from app.ai.streaming_platform import _StreamAccumulator
    from app.ai.errors import AIEmptyResponseError
    accumulator = _StreamAccumulator()
    accumulator.completed = True
    with pytest.raises(AIEmptyResponseError) as caught:
        accumulator.finalize(usage=ModelUsage(100, 7, 107, 80))
    assert caught.value.cached_input_tokens == 80


def test_fake_provider_rejected_usage_and_retry_both_preserve_cache(tmp_path):
    from app.ai.errors import AIEmptyResponseError
    class Platform:
        calls = 0
        def execute_chat(self, _profile, request):
            self.calls += 1
            if self.calls == 1:
                raise AIEmptyResponseError("reasoning only", reasoning_char_count=12,
                                           input_tokens=100, output_tokens=7, total_tokens=107,
                                           cached_input_tokens=80)
            return ModelResponse(text="Done", usage=ModelUsage(20, 2, 22, 10))
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=Platform(), store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        runtime.set_sticker_preferences({"frequency": 0})
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Finish")
        assert store.load(session.session_id).usage == ModelUsage(120, 9, 129, 90)
        rejected = [e for e in store.events(session.session_id) if e.kind.value == "model_response_rejected"]
        assert rejected[0].data["usage"]["cached_input_tokens"] == 80
    finally:
        runtime.close()


def test_anthropic_stream_cache_start_survives_output_delta(monkeypatch):
    import json
    from app.ai import AIMessage, ChatRequest, MessageRole
    from app.ai.opencode_go_runtime import _OpenCodeGoMessagesBackend
    class Stream:
        def __iter__(self):
            events = [
                {"type": "message_start", "message": {"id": "cache-test", "usage": {
                    "input_tokens": 10, "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 20, "output_tokens": 0}}},
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text", "text": "Done"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                 "usage": {"output_tokens": 7}},
                {"type": "message_stop"},
            ]
            return iter(line for e in events
                        for line in [("data: " + json.dumps(e) + "\n").encode(), b"\n"])
        def close(self):
            pass
    backend = _OpenCodeGoMessagesBackend(profile=NS(model="minimax-m3"),
                                         api_key="test-only-key", request_timeout_seconds=1)
    monkeypatch.setattr(backend, "_open", lambda *_a, **_k: Stream())
    list(backend.stream(ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="Check"),))))
    assert backend.last_stream_metadata()["usage"] == ModelUsage(110, 7, 117, 80)
