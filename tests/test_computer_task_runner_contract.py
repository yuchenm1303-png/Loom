from __future__ import annotations

from app.agent_runtime.computer_tools import computer_tools


class FakeComputerSessions:
    grounder = object()


class FakeComputerRuntime:
    computer_sessions = FakeComputerSessions()
    computer_grounder_name = "fake-grounder"

    def computer_status(self, _session_id=None):
        return {"enabled": True, "grounder": self.computer_grounder_name}

    def consume_computer_transient(self, value: str) -> str:
        return value


def test_computer_run_task_is_exposed_before_low_level_step():
    tools = computer_tools(FakeComputerRuntime())
    names = [tool.name for tool in tools]

    assert "computer_run_task" in names
    assert "computer_step" in names
    assert names.index("computer_run_task") < names.index("computer_step")

    runner = next(tool for tool in tools if tool.name == "computer_run_task")
    assert runner.effect.value == "sensitive"
    assert runner.input_schema["required"] == ["task"]
    assert runner.input_schema["properties"]["max_steps"]["maximum"] == 40
