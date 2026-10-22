from dataclasses import replace

import pytest

from app.ai import AIMessage, MessageRole
from app.agent_runtime import context_reducer as reducer


@pytest.fixture(autouse=True)
def clear_projection_memo():
    reducer._tool_projections.clear()
    yield
    reducer._tool_projections.clear()


def tool_message(content):
    return AIMessage(role=MessageRole.TOOL, name="read_file", tool_call_id="call-1", content=content)


@pytest.mark.parametrize("content", ['{"ok":true,"content":"small"}', "中文🙂" * 12_000],
                         ids=["small", "oversized-unicode"])
def test_repeated_projection_reuses_exact_output_and_stats(monkeypatch, content):
    source = tool_message(content)
    first, stats = reducer.reduce_tool_outputs((source,), per_output_token_limit=256)

    def unexpected(*args, **kwargs):
        raise AssertionError("unchanged history must not be re-encoded or re-truncated")

    monkeypatch.setattr(reducer, "approx_text_tokens", unexpected)
    monkeypatch.setattr(reducer, "_reduced_tool_content", unexpected)
    second, repeated_stats = reducer.reduce_tool_outputs((source,), per_output_token_limit=256)
    assert second[0] is first[0]
    assert repeated_stats == stats
    assert source.content == content
    assert second[0].tool_call_id == "call-1"


def test_changed_budget_and_same_call_id_new_message_do_not_reuse_stale_output():
    source = tool_message("A" * 12_000)
    small, _ = reducer.reduce_tool_outputs((source,), per_output_token_limit=256)
    large, _ = reducer.reduce_tool_outputs((source,), per_output_token_limit=1024)
    assert len(large[0].content) > len(small[0].content)
    changed = replace(source, content="new evidence")
    latest, stats = reducer.reduce_tool_outputs((changed,), per_output_token_limit=1024)
    assert latest[0] is changed
    assert stats.tool_outputs_reduced == 0
    restored, _ = reducer.reduce_tool_outputs((source,), per_output_token_limit=256)
    assert restored == small


def test_cached_previews_do_not_change_collapsing_or_non_tool_history():
    user = AIMessage(role=MessageRole.USER, content="user evidence " * 10_000)
    assistant = AIMessage(role=MessageRole.ASSISTANT, content="preserve reasoning")
    source = tool_message("evidence " * 10_000)
    history = (user, assistant, source)
    reducer.reduce_tool_outputs(history, per_output_token_limit=256)
    options = dict(per_output_token_limit=256, target_total_tokens=1,
                   estimate_total=lambda messages: sum(reducer.approx_text_tokens(m.content) for m in messages))
    warm, warm_stats = reducer.reduce_tool_outputs(history, **options)
    reducer._tool_projections.clear()
    cold, cold_stats = reducer.reduce_tool_outputs(history, **options)
    assert warm == cold
    assert warm_stats == cold_stats
    assert warm_stats.tool_outputs_collapsed == 1
    assert warm[0] is user and warm[1] is assistant
    assert source.content == "evidence " * 10_000


def test_projection_memo_evicts_old_history(monkeypatch):
    memo = reducer.MessageMemo(entries=2, budget=10_000)
    monkeypatch.setattr(reducer, "_tool_projections", memo)
    history = tuple(tool_message(str(index)) for index in range(3))
    reducer.reduce_tool_outputs(history, per_output_token_limit=256)
    assert len(memo) == 2
    assert memo.get(history[0]) is None
    assert memo.get(history[-1]) is not None
