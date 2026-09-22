from __future__ import annotations

from app.agent_runtime import AgentStatus, DurableAgentRuntime, FileAgentSessionStore
from app.agent_runtime.turn_response_validation import (
    invalid_terminal_response,
    merge_recovery_text,
)
from app.ai import ModelResponse


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _runtime(tmp_path, responses):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "state")
    platform = ScriptedPlatform(responses)
    runtime = DurableAgentRuntime(platform=platform, store=store)
    session = runtime.create_session("agent.fast", workspace_dir=workspace)
    return runtime, store, platform, session


def test_terminal_validator_rejects_unclosed_inline_code_and_emphasis() -> None:
    inline = ModelResponse(
        text="2. **Commit 2：feat(ai): 支持把内嵌`",
        finish_reason="stop",
    )
    emphasis = ModelResponse(
        text="2. **Commit 2：feat(ai): 支持把内嵌 reasoning",
        finish_reason="stop",
    )

    assert invalid_terminal_response(inline) == "unterminated_inline_code"
    assert invalid_terminal_response(emphasis) == "unterminated_emphasis"


def test_recovery_text_merges_suffix_without_losing_or_duplicating_prefix() -> None:
    partial = "建议方案：**Commit 2：支持把内嵌`"
    continuation = "reasoning` 配套完成。**"

    assert merge_recovery_text(partial, continuation) == (
        "建议方案：**Commit 2：支持把内嵌`reasoning` 配套完成。**"
    )
    assert merge_recovery_text("abc", "abcdef") == "abcdef"
    assert merge_recovery_text("hello wor", "world") == "hello world"


def test_turn_recovers_cut_inline_markdown_into_one_complete_final_answer(tmp_path) -> None:
    partial = "建议方案：**Commit 2：支持把内嵌`"
    continuation = "reasoning` 配套完成。**"
    runtime, store, platform, session = _runtime(
        tmp_path,
        [
            ModelResponse(text=partial, finish_reason="stop", reasoning="r1"),
            ModelResponse(text=continuation, finish_reason="stop", reasoning="r2"),
        ],
    )

    result = runtime.start_turn(session.session_id, "给我完整建议")
    restored = store.load(session.session_id)

    expected = "建议方案：**Commit 2：支持把内嵌`reasoning` 配套完成。**"
    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == expected
    assert restored.final_text == expected
    assert len(platform.requests) == 2
    # The retry sees the rejected prefix as assistant context, but the durable
    # final response is reconstructed as one self-contained message.
    retry_messages = platform.requests[1][1].messages
    assert any(getattr(message, "content", "") == partial for message in retry_messages)


def test_turn_recovers_an_unclosed_decision_block_before_commit(tmp_path) -> None:
    partial = (
        "请选择一个：\n"
        "```loom-decision\n"
        '{"title":"推送范围","options":[{"id":"A","title":"只推本次"}'
    )
    continuation = ',{"id":"B","title":"全部一起推"}]}\n```'
    runtime, store, _platform, session = _runtime(
        tmp_path,
        [
            ModelResponse(text=partial, finish_reason="stop"),
            ModelResponse(text=continuation, finish_reason="stop"),
        ],
    )

    result = runtime.start_turn(session.session_id, "给我推送选项")

    assert result.status is AgentStatus.COMPLETED
    assert "```loom-decision" in result.final_text
    assert '"id":"A"' in result.final_text
    assert '"id":"B"' in result.final_text
    assert result.final_text.endswith("```")
