from __future__ import annotations

from collections.abc import Sequence

from app.ai import ToolCall


def _category(calls: Sequence[ToolCall]) -> str:
    names = {str(call.name or "").casefold() for call in calls}
    if any(name in {"exec", "exec_command", "write_stdin"} or "process" in name for name in names):
        return "command"
    if any("browser" in name or "computer" in name for name in names):
        return "interface"
    if any("read" in name or "search" in name or "list" in name for name in names):
        return "inspect"
    if any("patch" in name or "write" in name or "replace" in name for name in names):
        return "edit"
    if any("agent" in name for name in names):
        return "agent"
    return "tool"


def runtime_tool_commentary(
    calls: Sequence[ToolCall],
    *,
    communication_language: str,
    continuing: bool,
) -> str:
    """Return a safe public preamble when a model emits tools without prose.

    Arguments are intentionally ignored: command lines and tool payloads may contain
    credentials or other data that must not be reflected into the transcript.
    """

    category = _category(calls)
    zh = str(communication_language or "").casefold() == "zh"
    if zh:
        prefix = "我继续" if continuing else "我先"
        messages = {
            "command": "检查当前环境和命令结果，再根据证据推进下一步。",
            "interface": "检查当前界面状态并执行下一步操作，有结果后马上同步。",
            "inspect": "定位相关信息和现状，再根据结果继续处理。",
            "edit": "完成必要修改并立即验证结果。",
            "agent": "协调子任务并汇总结果，随后同步进展。",
            "tool": "执行下一步必要检查，有结果后马上同步。",
        }
    else:
        prefix = "I'll continue: " if continuing else "I'll "
        messages = {
            "command": "check the environment and command results, then proceed from the evidence.",
            "interface": "inspect the current interface and take the next action, then report back.",
            "inspect": "locate the relevant information and current state, then continue from the result.",
            "edit": "make the necessary change and verify it immediately.",
            "agent": "coordinate the subtask and report the combined progress.",
            "tool": "run the next necessary check and report back with the result.",
        }
    return prefix + messages[category]


__all__ = ["runtime_tool_commentary"]
