from types import SimpleNamespace

from app.ai import AIMessage, MessageRole, ToolCall
from app.agent_runtime import context_budget as budget
from app.agent_runtime.context_composer import render_request
from app.agent_runtime.stickers import INLINE_STICKER_STRUCTURED_PLAN_BEGIN as BEGIN, INLINE_STICKER_STRUCTURED_PLAN_END as END


def render(history):
    return render_request(SimpleNamespace(), SimpleNamespace(session_id="s", request_context_frames=[]), (), history)


def test_unchanged_history_reuses_measurements_and_retains_protocol(monkeypatch):
    history = (AIMessage(role=MessageRole.USER, content="Exact user evidence"),
               AIMessage(role=MessageRole.ASSISTANT, content="Inspect", reasoning="retained",
                         tool_calls=(ToolCall("c", "read_file", {"path": "x"}),)),
               AIMessage(role=MessageRole.TOOL, content="untrusted evidence", name="read_file", tool_call_id="c"))
    budget._measurements.clear()
    first = render(history)
    expected = budget.estimate_tokens(first)
    def unexpected(*args):
        raise AssertionError("unchanged completed history must not be serialized again")
    monkeypatch.setattr(budget, "_measure_message", unexpected)
    repeated = render(history)
    assert all(a is b for a, b in zip(history, repeated))
    assert budget.estimate_tokens(repeated) == expected
    assert repeated == first


def test_legacy_projection_changes_only_content_and_new_evidence_is_measured():
    source = AIMessage(role=MessageRole.ASSISTANT, content=f"before{BEGIN}old control{END}after",
                       reasoning="retain", tool_calls=(ToolCall("c", "read_file", {}),))
    projected = render((source,))[0]
    assert projected is not source
    assert projected.content == "beforeafter"
    assert projected.reasoning == source.reasoning and projected.tool_calls == source.tool_calls
    assert "old control" in source.content
    changed = AIMessage(role=MessageRole.ASSISTANT, content="new evidence" * 100)
    assert render((changed,))[0] is changed
    assert budget.estimate_tokens(render((changed,))) != budget.estimate_tokens((projected,))
