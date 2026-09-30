from __future__ import annotations

import pytest

from app.agent_runtime import AgentStatus, DurableAgentRuntime, FileAgentSessionStore
from app.agent_runtime.turn_response_validation import (
    invalid_terminal_response,
    merge_recovery_text,
)
from app.ai import ModelResponse


PROMISE = (
    '注意到"蚂蚁百灵"这个搜索结果——它指向的就是 ant-ling 平台本身。'
    '我先去 Hugging Face 的 Ling 官方仓库，那里通常写明推理接入方式'
    '（OpenAI 兼容 base URL + 模型名）。[[AI_LEDGER_INLINE_STICKER:joy_burst]]'
)


@pytest.mark.parametrize("text", [
    PROMISE,
    "我先检查日志。",
    "接下来我会运行测试。",
    "我现在先修复这个错误。",
    "I'll check the repository now.",
    "Let me look at the logs.",
])
def test_progress_only_terminal_response_requires_continuation(text):
    assert invalid_terminal_response(ModelResponse(text=text, finish_reason="stop")) == "unfulfilled_action_promise"


@pytest.mark.parametrize("text", [
    "你可以先检查日志。",
    "要不要我再检查一次？",
    "如果需要，我会检查日志。",
    "我先检查日志。检查结果：没有错误。",
    "我会运行测试。验证已完成。",
    '模型回复了“我先检查日志。”，随后结束。',
    "> 我先检查日志。",
    "```text\n我先检查日志。\n```",
    "Next, you can run the tests.",
    "I'll check the logs. I found no errors.",
    "修复已完成，测试通过。",
])
def test_completed_answers_and_quoted_promises_are_accepted(text):
    assert invalid_terminal_response(ModelResponse(text=text, finish_reason="stop")) == ""


def test_action_promise_retry_replaces_progress_and_preserves_same_turn(tmp_path):
    runtime, store, platform, session = _runtime(tmp_path, [
        ModelResponse(text=PROMISE, finish_reason="stop", reasoning="inspect repository"),
        ModelResponse(text="查询完成，API 文档地址已确认。", finish_reason="stop"),
    ])
    result = runtime.start_turn(session.session_id, "直接查资料")
    restored = store.load(session.session_id)
    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "查询完成，API 文档地址已确认。"
    assert restored.current_turn_id == result.turn_id
    assert all(PROMISE != message.content for message in restored.messages)
    replay, instruction = platform.requests[1][1].messages[-2:]
    assert replay.content == PROMISE
    assert replay.reasoning == "inspect repository"
    assert "promised action" in instruction.content
    assert sum(e.kind.value == "turn_completed" for e in store.events(session.session_id)) == 1
    runtime.close()


def test_repeated_action_promises_fail_instead_of_claiming_completion(tmp_path):
    runtime, store, platform, session = _runtime(tmp_path, [
        ModelResponse(text=PROMISE, finish_reason="stop") for _ in range(3)
    ])
    result = runtime.start_turn(session.session_id, "直接查资料")
    assert result.status is AgentStatus.FAILED
    assert "unfulfilled_action_promise" in result.error
    assert len(platform.requests) == 3
    assert not any(e.kind.value == "turn_completed" for e in store.events(session.session_id))
    runtime.close()


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


def test_terminal_validator_accepts_imperfect_markdown_in_completed_answer() -> None:
    inline = ModelResponse(
        text="2. **Commit 2：feat(ai): 支持把内嵌`",
        finish_reason="stop",
    )
    emphasis = ModelResponse(
        text="2. **Commit 2：feat(ai): 支持把内嵌 reasoning",
        finish_reason="stop",
    )

    assert invalid_terminal_response(inline) == ""
    assert invalid_terminal_response(emphasis) == ""


def test_recovery_text_merges_suffix_without_losing_or_duplicating_prefix() -> None:
    partial = "建议方案：**Commit 2：支持把内嵌`"
    continuation = "reasoning` 配套完成。**"

    assert merge_recovery_text(partial, continuation) == (
        "建议方案：**Commit 2：支持把内嵌`reasoning` 配套完成。**"
    )
    assert merge_recovery_text("abc", "abcdef") == "abcdef"
    assert merge_recovery_text("hello wor", "world") == "hello world"


def test_turn_commits_completed_answer_without_retrying_for_inline_markdown(tmp_path) -> None:
    partial = "建议方案：**Commit 2：支持把内嵌`"
    runtime, store, platform, session = _runtime(
        tmp_path,
        [
            ModelResponse(text=partial, finish_reason="stop", reasoning="r1"),
        ],
    )

    result = runtime.start_turn(session.session_id, "给我完整建议")
    restored = store.load(session.session_id)

    expected = partial
    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == expected
    assert restored.final_text == expected
    assert len(platform.requests) == 1


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
