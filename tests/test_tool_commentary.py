from __future__ import annotations

from app.ai import ToolCall
from app.agent_runtime.tool_commentary import runtime_tool_commentary


def _call(name: str, arguments=None) -> ToolCall:
    return ToolCall(call_id=f"call-{name}", name=name, arguments=arguments or {})


def test_runtime_tool_commentary_is_language_aware_and_never_reflects_arguments():
    secret = "super-secret-password"
    text = runtime_tool_commentary(
        [_call("exec", {"cmd": f"ssh user:{secret}@host"})],
        communication_language="zh",
        continuing=False,
    )

    assert text.startswith("我先")
    assert "命令结果" in text
    assert secret not in text


def test_runtime_tool_commentary_describes_continuation_without_tool_jargon():
    text = runtime_tool_commentary(
        [_call("read_workspace_text")],
        communication_language="latin",
        continuing=True,
    )

    assert text.startswith("I'll continue")
    assert "read_workspace_text" not in text
