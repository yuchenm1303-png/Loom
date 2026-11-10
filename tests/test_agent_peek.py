"""A conversation's sub-agents, looked at: what one is doing now (peek) and every one it ever had (history)."""
from __future__ import annotations

import time

import pytest

from app.ai import ModelResponse, ToolCall
from test_project_goal_lines import (  # noqa: F401  the autouse fixture and the scripted-model helpers
    answer, command_tool, conversation, line, run_cmd, runtime_with_sub_agents, split, START, tick, usage, wait_lines)

def test_a_sub_agent_can_be_peeked_at_while_running_and_after_it_was_closed(tmp_path):
    from app.project_runner import agent_peek
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["results/甲"])), answer("开始"), answer("整合")],
        {"甲": [ModelResponse(text="先读 README，再跑检查", tool_calls=(ToolCall(call_id="c1", name="run_cmd",
                                                                           arguments={"cmd": "python check.py 甲", "code": 0}),), usage=usage()),
                answer("甲的报告：吞吐 +6.1%")]}, tools=(command_tool(),))
    try:
        wait_lines(service, source)
        agent = service.weave_goals.get(source)["lines"][0]["agentId"]
        shown = agent_peek.peek(service, {"threadId": source, "agentId": agent})
        assert shown["role"] == "goal-line" and shown["finished"] is True
        kinds = [(row["kind"], row.get("ok")) for row in shown["rows"]]
        assert ("tool", True) in kinds and any(kind == "say" for kind, _ in kinds), shown["rows"]
        assert any("check.py" in row["text"] for row in shown["rows"] if row["kind"] == "tool")
        assert "吞吐 +6.1%" in shown["result"] and shown["tokens"] > 0
        tick(service, source)  # the Host settles and closes the line
        closed = agent_peek.peek(service, {"threadId": source, "agentId": agent})
        assert closed["finished"] is True and closed["outcome"] == "done" and closed["endedAt"]
        with pytest.raises(ValueError, match="does not belong"):
            agent_peek.peek(service, {"threadId": source, "agentId": source})
        with pytest.raises(ValueError, match="does not belong"):
            agent_peek.peek(service, {"threadId": source, "agentId": "someone-else"})
    finally:
        runtime.close()


def test_the_history_keeps_every_sub_agent_of_the_conversation_across_goals(tmp_path):
    from app.project_runner import agent_peek
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["results/甲"]), line("乙")), answer("开始"), answer("整合")],
        {"甲": [run_cmd("python check.py 甲", 0, "c1"), answer("甲的报告")], "乙": [answer("乙的报告")]}, tools=(command_tool(),))
    try:
        wait_lines(service, source)
        tick(service, source)
        record = service.weave_goals.get(source)
        service.weave_goals.put(source, {**record, "startedAt": time.time() + 3600})  # a new goal began: the live view forgets them
        assert service._conversation_agent_views(source)["conversationAgents"] == []
        shown = agent_peek.history(service, {"threadId": source})
        assert shown["total"] == 2 and {row["task"] for row in shown["agents"]} == {"甲", "乙"}
        assert all(row["finished"] and row["outcome"] == "done" and row["endedAt"] for row in shown["agents"])
        assert [row["startedAt"] for row in shown["agents"]] == sorted((row["startedAt"] for row in shown["agents"]), reverse=True)
        assert any("甲的报告" in row["result"] for row in shown["agents"])
        assert agent_peek.history(service, {"threadId": source, "offset": 1})["agents"][0]["id"] == shown["agents"][1]["id"]
        assert set(service._agent_history_rows) == {row["id"] for row in shown["agents"]}, "closed agents are read once"
    finally:
        runtime.close()
