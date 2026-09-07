"""Pure formatting helpers for the desktop client.

Nothing here imports Qt. The window, the widgets and the tests all share these
so a label, a tooltip and an activity row cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted", "limit_reached"}
)
ACTIVE_STATUSES = frozenset({"running", "starting", "waiting_approval"})

AGENT_CONTROL_TOOLS = frozenset(
    {
        "spawn_agent",
        "send_agent_message",
        "wait_agent",
        "list_agents",
        "close_agent",
    }
)


def text(value: Any) -> str:
    return str(value or "")


def pretty(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def short_path(value: Any) -> str:
    raw = text(value).strip()
    if not raw:
        return "No workspace"
    return Path(raw).name or raw


def same_path(left: Any, right: Any) -> bool:
    left_raw, right_raw = text(left).strip(), text(right).strip()
    if not left_raw or not right_raw:
        return False
    try:
        return Path(left_raw).resolve() == Path(right_raw).resolve()
    except OSError:
        return left_raw == right_raw


def human_status(value: Any) -> str:
    return (text(value).strip() or "idle").replace("_", " ").title()


def short_time(value: Any) -> str:
    raw = text(value).strip()
    if "T" in raw:
        return raw.split("T", 1)[1][:8]
    return raw[-8:] if len(raw) >= 8 else raw


def format_tokens(value: Any) -> str:
    try:
        tokens = int(value or 0)
    except (TypeError, ValueError):
        tokens = 0
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(tokens)


def format_duration(milliseconds: Any) -> str:
    try:
        value = float(milliseconds or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value < 1000:
        return f"{int(value)}ms"
    if value < 60_000:
        return f"{value / 1000:.1f}s".replace(".0s", "s")
    minutes, seconds = divmod(int(value / 1000), 60)
    return f"{minutes}m{seconds:02d}s"


def command_line(argv: Any) -> str:
    if isinstance(argv, (list, tuple)):
        return " ".join(str(part) for part in argv)
    return text(argv)


def event_summary(event: dict[str, Any]) -> str:
    kind = text(event.get("kind")) or "event"
    data = event.get("data")
    if not isinstance(data, dict):
        data = {}

    simple = {
        "session_created": "Session started",
        "user_message": "Prompt received",
        "turn_started": "Turn started",
        "turn_completed": "Turn completed",
        "turn_failed": "Turn failed",
    }
    if kind in simple:
        return simple[kind]
    if kind == "model_requested":
        return f"Asked model · step {data.get('step', '?')}"
    if kind == "model_response":
        usage = data.get("usage")
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        return "Model replied" + (f" · {format_tokens(total)} tokens" if total else "")
    if kind == "turn_diff_updated":
        count = len(data.get("paths") or [])
        return f"Changed {count} file{'' if count == 1 else 's'}"
    if kind == "tool_approval_required":
        return f"Approval needed · {text(data.get('tool')) or 'tool'}"
    if kind.startswith("tool_"):
        tool = text(data.get("tool")) or "tool"
        if kind == "tool_started":
            return f"Running {tool}"
        if kind == "tool_completed":
            return f"Finished {tool}"
        if kind == "tool_failed":
            return f"Tool failed · {tool}"
        return f"{kind.replace('_', ' ')} · {tool}"
    if kind.startswith("process_"):
        process_id = text(data.get("process_id"))[:10]
        suffix = f" · {process_id}" if process_id else ""
        if kind == "process_started":
            return f"Process started{suffix}"
        if kind == "process_exited":
            return f"Process finished{suffix}"
        return f"{kind.replace('_', ' ')}{suffix}"
    return kind.replace("_", " ").capitalize()


def event_marker(kind: Any) -> str:
    value = text(kind)
    if value in {"turn_completed", "tool_completed", "process_exited"}:
        return "✓"
    if value in {"turn_failed", "tool_failed"}:
        return "!"
    if value.startswith("model_"):
        return "◆"
    if value.startswith("tool_"):
        return "◇"
    if value.startswith("process_"):
        return "$"
    if value == "turn_diff_updated":
        return "Δ"
    if value == "turn_started":
        return "→"
    return "•"


def marker_tone(marker: str) -> str:
    if marker == "✓":
        return "good"
    if marker == "!":
        return "bad"
    if marker in {"◆", "◇", "Δ"}:
        return "accent"
    return "muted"


def notification_summary(method: str, params: dict[str, Any]) -> str:
    if method == "item/delta":
        delta = params.get("delta")
        if isinstance(delta, dict):
            if "text" in delta:
                return "Loom is responding"
            if "stdout" in delta or "stderr" in delta:
                return "Process output"
            if delta.get("kind") == "tool_call_argument":
                return f"Preparing {text(delta.get('toolName')) or 'tool'}"
            if delta.get("status"):
                return f"Item · {delta.get('status')}"
    if method == "approval/requested":
        approval = params.get("approval") or {}
        name = text(approval.get("toolName")) if isinstance(approval, dict) else ""
        return f"Approval needed · {name or 'tool'}"
    if method == "turn/completed":
        turn = params.get("turn") or {}
        status = text(turn.get("status")) if isinstance(turn, dict) else ""
        return f"Turn {status or 'completed'}"
    if method == "turn/started":
        return "Turn started"
    return method.replace("/", " · ")


__all__ = [
    "ACTIVE_STATUSES",
    "AGENT_CONTROL_TOOLS",
    "TERMINAL_STATUSES",
    "command_line",
    "event_marker",
    "event_summary",
    "format_duration",
    "format_tokens",
    "human_status",
    "marker_tone",
    "notification_summary",
    "pretty",
    "same_path",
    "short_path",
    "short_time",
    "text",
]
