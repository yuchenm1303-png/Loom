from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_runtime import AgentStatus, DurableAgentRuntime, FileAgentSessionStore
from app.agent_runtime.turn_response_validation import (
    invalid_terminal_response,
    merge_recovery_text,
)
from app.ai import ModelResponse


DECISION_CASES = json.loads((Path(__file__).parent / "fixtures/decision_protocol.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DECISION_CASES, ids=lambda case: case["name"])
def test_decision_protocol_matches_shared_frontend_contract(case):
    reason = invalid_terminal_response(ModelResponse(text=case["text"], finish_reason="stop"))
    assert reason == ("" if case["valid"] else "incomplete_decision_block")


def test_production_runtime_recovers_decision_without_losing_partial(tmp_path):
    from app.agent_runtime import AgentRuntime, SandboxManager, SandboxPolicy
    from test_agent_request_layout import ScriptedPlatform
    partial = '```loom-decision\n{"title":"Choose","options":[{"id":"A","title":"First"}'
    suffix = ',{"id":"B","title":"Second"}]}\n```'
    platform = ScriptedPlatform([ModelResponse(text=partial), ModelResponse(text=suffix)])
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        result = runtime.start_turn(session.session_id, "Choose")
        assert result.status is AgentStatus.COMPLETED
        assert partial + suffix in result.final_text
        assert any(m.name == "loom_terminal_recovery" and partial in str(m.content)
                   for m in platform.requests[1].messages)
        assert any("incomplete or invalid Loom decision card" in str(m.content)
                   for m in platform.requests[1].messages)
        assert any(e.data.get("reason") == "incomplete_decision_block" for e in runtime.store.events(session.session_id))
    finally:
        runtime.close()


@pytest.mark.parametrize("text", ["等一下。", "我先检查日志。", "Let me look at the logs.", "检查完成。"])
def test_terminal_format_validation_does_not_guess_task_completion(text):
    assert invalid_terminal_response(ModelResponse(text=text, finish_reason="stop")) == ""


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
