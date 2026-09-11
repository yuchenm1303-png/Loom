from __future__ import annotations

import pytest

from app.agent_runtime.computer_transient import ComputerTransientInputPlatform
from app.ai import ModelResponse, ToolCall


class TaskRunnerDelegate:
    def execute_chat(self, _profile_id, _request):
        return ModelResponse(
            tool_calls=(
                ToolCall(
                    call_id="call-1",
                    name="computer_run_task",
                    arguments={
                        "task": "open WeChat and search a private contact",
                        "stop_when": "the private contact is visible",
                        "max_steps": 6,
                    },
                ),
            )
        )


def test_computer_run_task_text_is_transient():
    platform = ComputerTransientInputPlatform(TaskRunnerDelegate())

    response = platform.execute_chat("agent-fast", object())

    call = response.tool_calls[0]
    assert call.name == "computer_run_task"
    assert call.arguments["task"].startswith("loom-transient-computer:")
    assert call.arguments["stop_when"].startswith("loom-transient-computer:")
    assert call.arguments["max_steps"] == 6
    assert platform.consume(call.arguments["task"]) == "open WeChat and search a private contact"
    assert platform.consume(call.arguments["stop_when"]) == "the private contact is visible"

    with pytest.raises(RuntimeError):
        platform.consume(call.arguments["task"])
