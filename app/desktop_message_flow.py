from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


_ACTIVE_STATUSES = {"running", "starting", "streaming", "waiting", "waiting_approval"}
_EXEC_TOOLS = {"exec", "run_workspace_command", "run_command", "shell", "terminal"}
_CONTEXT_EVENT_KINDS = {
    "context_checkpointed",
    "history_repaired",
    "memory_extracted",
    "memory_consolidated",
    "goal_updated",
}


def _text(value: Any) -> str:
    return str(value or "")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_time(value: Any) -> datetime | None:
    raw = _text(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    minutes, sec = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h {minute}m" if minute else f"{hours}h"


def elapsed_label(
    started_at: Any,
    completed_at: Any,
    status: Any,
    *,
    now: datetime | None = None,
) -> str:
    start = _parse_time(started_at)
    if start is None:
        return "Working" if _text(status).casefold() in _ACTIVE_STATUSES else "Activity"
    end = _parse_time(completed_at)
    active = _text(status).casefold() in _ACTIVE_STATUSES and end is None
    if end is None:
        end = now or datetime.now(timezone.utc)
    duration = _format_duration(max(0.0, (end - start).total_seconds()))
    return f"Working · {duration}" if active else f"Worked for {duration}"


def _compact_json(value: Any, *, limit: int = 6_000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        text = _text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 80] + "\n… [truncated by desktop renderer]"


def _command(argv: Any) -> str:
    if not isinstance(argv, (list, tuple)):
        return _text(argv).strip()
    parts = [str(part) for part in argv]
    text = " ".join(parts).strip()
    return text if len(text) <= 220 else text[:217] + "…"


def _argument_hint(arguments: Mapping[str, Any]) -> str:
    for key in (
        "url",
        "query",
        "path",
        "cwd",
        "task",
        "process_id",
        "processId",
        "selector",
    ):
        value = arguments.get(key)
        if value is not None and value != "" and value != []:
            text = _text(value).replace("\n", " ").strip()
            return text if len(text) <= 170 else text[:167] + "…"
    argv = arguments.get("argv")
    if isinstance(argv, (list, tuple)) and argv:
        return _command(argv)
    return ""


def _human_tool_name(name: str) -> str:
    words = name.replace("-", "_").split("_")
    return " ".join(word for word in words if word).strip().title() or "Tool"


@dataclass(frozen=True, slots=True)
class FlowItem:
    item_id: str
    turn_id: str
    kind: str
    status: str
    created_at: str
    updated_at: str
    title: str
    detail: str = ""
    body: str = ""
    marker: str = "•"
    tone: str = "muted"
    text: str = ""
    streaming: bool = False
    expandable: bool = False


@dataclass(frozen=True, slots=True)
class FlowTurn:
    turn_id: str
    status: str
    started_at: str
    completed_at: str
    source: str
    items: tuple[FlowItem, ...]

    def elapsed(self, *, now: datetime | None = None) -> str:
        return elapsed_label(self.started_at, self.completed_at, self.status, now=now)


def _process_item(raw: Mapping[str, Any]) -> FlowItem:
    status = _text(raw.get("status")) or "running"
    argv = raw.get("argv") or []
    command = _command(argv)
    returncode = raw.get("returncode")
    if status in _ACTIVE_STATUSES:
        title = "Running command"
        tone = "active"
    elif returncode not in {None, 0}:
        title = f"Command exited · {returncode}"
        tone = "bad"
    else:
        title = "Ran command"
        tone = "good"
    detail = f"$ {command}" if command else _text(raw.get("processId"))
    body: list[str] = []
    cwd = _text(raw.get("cwd")).strip()
    if cwd:
        body.append(f"cwd: {cwd}")
    sandbox = _mapping(raw.get("sandbox"))
    if sandbox:
        enforced = bool(sandbox.get("enforced"))
        backend = _text(sandbox.get("backend")) or "none"
        body.append(f"sandbox: {'enforced' if enforced else 'not enforced'} · {backend}")
    stdout = _text(raw.get("stdout"))
    stderr = _text(raw.get("stderr"))
    if stdout:
        body.append("stdout:\n" + stdout[-5_000:].rstrip())
    if stderr:
        body.append("stderr:\n" + stderr[-5_000:].rstrip())
    return FlowItem(
        item_id=_text(raw.get("id")) or f"process:{_text(raw.get('processId'))}",
        turn_id=_text(raw.get("turnId")),
        kind="process",
        status=status,
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        title=title,
        detail=detail,
        body="\n\n".join(body),
        marker=">_",
        tone=tone,
        expandable=bool(body),
    )


def _tool_item(raw: Mapping[str, Any]) -> FlowItem:
    status = _text(raw.get("status")) or "started"
    name = _text(raw.get("toolName")) or "tool"
    lowered = name.casefold()
    arguments = _mapping(raw.get("arguments"))
    hint = _argument_hint(arguments)
    running = status in _ACTIVE_STATUSES
    failed = status in {"failed", "denied"} or raw.get("ok") is False

    if lowered in _EXEC_TOOLS:
        title = "Preparing command" if running else "Command tool"
        marker = ">_"
        category = "command"
    elif "browser" in lowered:
        title = f"Browser · {_human_tool_name(name.replace('browser_', '', 1))}"
        marker = "◎"
        category = "browser"
    elif "web_search" in lowered or lowered in {"search_web", "web_search"}:
        title = "Searching the web" if running else "Searched the web"
        marker = "⌕"
        category = "browser"
    elif "computer" in lowered or "desktop" in lowered:
        title = f"Computer · {_human_tool_name(name)}"
        marker = "⌁"
        category = "computer"
    elif lowered in {
        "spawn_agent",
        "send_agent_message",
        "wait_agent",
        "list_agents",
        "close_agent",
    } or "agent" in lowered:
        title = f"Agent · {_human_tool_name(name)}"
        marker = "◇"
        category = "agent"
    elif "mcp" in lowered:
        title = f"MCP · {_human_tool_name(name)}"
        marker = "◇"
        category = "tool"
    elif lowered == "tool_search" or "tool_search" in lowered:
        title = "Searching tools" if running else "Searched tools"
        marker = "⌕"
        category = "tool"
    elif "patch" in lowered or "edit" in lowered or "write_file" in lowered:
        title = f"Workspace · {_human_tool_name(name)}"
        marker = "Δ"
        category = "file"
    else:
        title = (
            f"Using {_human_tool_name(name)}"
            if running
            else f"Used {_human_tool_name(name)}"
        )
        marker = "◇"
        category = "tool"

    tone = "bad" if failed else "active" if running else "good"
    body_parts: list[str] = []
    if arguments:
        body_parts.append("arguments:\n" + _compact_json(arguments))
    content = _text(raw.get("content") or raw.get("reason"))
    if content:
        body_parts.append("result:\n" + content[-4_000:])
    result = raw.get("result")
    if result is not None and result != {} and result != []:
        body_parts.append("data:\n" + _compact_json(result, limit=4_000))
    return FlowItem(
        item_id=_text(raw.get("id")) or f"tool:{_text(raw.get('callId'))}",
        turn_id=_text(raw.get("turnId")),
        kind=category,
        status=status,
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        title=title,
        detail=hint,
        body="\n\n".join(body_parts),
        marker=marker,
        tone=tone,
        expandable=bool(body_parts),
    )


def _file_item(raw: Mapping[str, Any]) -> FlowItem:
    paths = [str(path) for path in (raw.get("paths") or [])]
    status = _text(raw.get("status")) or "completed"
    count = len(paths)
    title = (
        f"Changed {count} file{'s' if count != 1 else ''}"
        if count
        else "Workspace changed"
    )
    detail = ", ".join(paths[:4])
    if len(paths) > 4:
        detail += f" · +{len(paths) - 4} more"
    diff = _text(raw.get("diff"))
    body = "paths:\n" + "\n".join(paths) if paths else ""
    if diff:
        body += ("\n\n" if body else "") + "diff:\n" + diff[-6_000:]
    return FlowItem(
        item_id=_text(raw.get("id")),
        turn_id=_text(raw.get("turnId")),
        kind="file",
        status=status,
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        title=title,
        detail=detail,
        body=body,
        marker="Δ",
        tone="good",
        expandable=bool(body),
    )


def _approval_item(raw: Mapping[str, Any]) -> FlowItem:
    status = _text(raw.get("status")) or "waiting"
    tool = _text(raw.get("toolName")) or "tool"
    waiting = status in {"waiting", "waiting_approval", "started"}
    denied = status == "denied"
    title = (
        f"Approval required · {_human_tool_name(tool)}"
        if waiting
        else f"Approval denied · {_human_tool_name(tool)}"
        if denied
        else f"Approved · {_human_tool_name(tool)}"
    )
    arguments = _mapping(raw.get("arguments"))
    body_parts: list[str] = []
    reason = _text(raw.get("reason"))
    if reason:
        body_parts.append(reason)
    if arguments:
        body_parts.append(_compact_json(arguments))
    return FlowItem(
        item_id=_text(raw.get("id")) or f"approval:{_text(raw.get('callId'))}",
        turn_id=_text(raw.get("turnId")),
        kind="approval",
        status=status,
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        title=title,
        detail=_argument_hint(arguments),
        body="\n\n".join(body_parts),
        marker="!",
        tone="bad" if denied else "warn" if waiting else "good",
        expandable=bool(body_parts),
    )


def _chat_item(raw: Mapping[str, Any], *, assistant: bool) -> FlowItem:
    status = _text(raw.get("status")) or "completed"
    return FlowItem(
        item_id=_text(raw.get("id")),
        turn_id=_text(raw.get("turnId")),
        kind="assistant" if assistant else "user",
        status=status,
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        title="",
        text=_text(raw.get("text")),
        tone="accent" if assistant else "user",
        streaming=status in _ACTIVE_STATUSES,
    )


def _error_item(raw: Mapping[str, Any]) -> FlowItem:
    error = _text(raw.get("error")) or "Turn failed"
    return FlowItem(
        item_id=_text(raw.get("id")),
        turn_id=_text(raw.get("turnId")),
        kind="error",
        status=_text(raw.get("status")) or "failed",
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        title="Runtime error",
        detail=error.splitlines()[0][:180],
        body=error,
        marker="!",
        tone="bad",
        expandable=True,
    )


def item_from_record(raw: Mapping[str, Any]) -> FlowItem | None:
    item_type = _text(raw.get("type"))
    if item_type == "user_message":
        return _chat_item(raw, assistant=False)
    if item_type == "assistant_message":
        return _chat_item(raw, assistant=True)
    if item_type == "process":
        return _process_item(raw)
    if item_type == "tool_call":
        return _tool_item(raw)
    if item_type == "file_edit":
        return _file_item(raw)
    if item_type == "approval":
        return _approval_item(raw)
    if item_type == "error":
        return _error_item(raw)
    return None


def _context_item(event: Mapping[str, Any]) -> FlowItem | None:
    kind = _text(event.get("kind"))
    if kind not in _CONTEXT_EVENT_KINDS:
        return None
    data = _mapping(event.get("data"))
    if kind == "context_checkpointed":
        source = _text(data.get("summary_source"))
        title = (
            "Context automatically compacted"
            if source == "model"
            else "Context checkpointed"
        )
        archived = int(data.get("archived_messages") or 0)
        retained = int(data.get("retained_messages") or 0)
        detail = f"archived {archived} · retained {retained}"
        marker = "↯"
    elif kind == "history_repaired":
        title = "Conversation history repaired"
        detail = "Canonical tool history normalized"
        marker = "↯"
    elif kind == "memory_extracted":
        title = "Memory extracted"
        detail = "Durable memory candidates updated"
        marker = "◇"
    elif kind == "memory_consolidated":
        title = "Memory consolidated"
        detail = "Durable memory records compacted"
        marker = "◇"
    else:
        title = "Task goal updated"
        detail = _text(data.get("objective"))[:180]
        marker = "◇"
    return FlowItem(
        item_id=f"event:{_text(event.get('eventId'))}",
        turn_id=_text(event.get("turnId")),
        kind="context",
        status="completed",
        created_at=_text(event.get("createdAt")),
        updated_at=_text(event.get("createdAt")),
        title=title,
        detail=detail,
        body=_compact_json(data) if data else "",
        marker=marker,
        tone="muted",
        expandable=bool(data),
    )


def _sort_items(items: Iterable[FlowItem]) -> tuple[FlowItem, ...]:
    decorated = list(enumerate(items))
    decorated.sort(
        key=lambda pair: (
            _parse_time(pair[1].created_at)
            or datetime.max.replace(tzinfo=timezone.utc),
            pair[0],
        )
    )
    return tuple(item for _, item in decorated)


def _dedupe_exec_tools(
    items: Iterable[FlowItem],
    raw_items: Iterable[Mapping[str, Any]],
) -> tuple[FlowItem, ...]:
    records = list(raw_items)
    process_commands = {
        tuple(str(part) for part in (raw.get("argv") or []))
        for raw in records
        if _text(raw.get("type")) == "process"
        and isinstance(raw.get("argv"), (list, tuple))
    }
    output: list[FlowItem] = []
    for item in items:
        if item.kind != "command" or not item.item_id.startswith("tool:"):
            output.append(item)
            continue
        raw_match = next(
            (raw for raw in records if _text(raw.get("id")) == item.item_id),
            None,
        )
        if raw_match is None:
            output.append(item)
            continue
        argv = _mapping(raw_match.get("arguments")).get("argv")
        if (
            isinstance(argv, (list, tuple))
            and tuple(str(part) for part in argv) in process_commands
        ):
            continue
        output.append(item)
    return tuple(output)


def build_message_flow(
    snapshot: Mapping[str, Any],
    *,
    live_items: Iterable[Mapping[str, Any]] = (),
    live_turns: Mapping[str, Mapping[str, Any]] | None = None,
    live_assistant: Mapping[str, str] | None = None,
    optimistic_user: str | None = None,
) -> tuple[FlowTurn, ...]:
    raw_turns = [
        dict(turn)
        for turn in (snapshot.get("turns") or [])
        if isinstance(turn, Mapping)
    ]
    live_turns = dict(live_turns or {})
    live_records = [dict(item) for item in live_items if isinstance(item, Mapping)]

    turn_map: dict[str, FlowTurn] = {}
    order: list[str] = []

    for raw in raw_turns:
        turn_id = _text(raw.get("id"))
        if not turn_id:
            continue
        order.append(turn_id)
        records = [
            dict(item)
            for item in (raw.get("items") or [])
            if isinstance(item, Mapping)
        ]
        mapped = [item_from_record(record) for record in records]
        items = tuple(item for item in mapped if item is not None)
        turn_map[turn_id] = FlowTurn(
            turn_id=turn_id,
            status=_text(raw.get("status")) or "completed",
            started_at=_text(raw.get("startedAt")),
            completed_at=_text(raw.get("completedAt")),
            source=_text(raw.get("source")) or "user",
            items=_sort_items(_dedupe_exec_tools(items, records)),
        )

    for turn_id, raw in live_turns.items():
        if turn_id in turn_map:
            current = turn_map[turn_id]
            turn_map[turn_id] = replace(
                current,
                status=_text(raw.get("status")) or current.status,
                started_at=_text(raw.get("startedAt")) or current.started_at,
                completed_at=_text(raw.get("completedAt")) or current.completed_at,
            )
        else:
            order.append(turn_id)
            turn_map[turn_id] = FlowTurn(
                turn_id=turn_id,
                status=_text(raw.get("status")) or "running",
                started_at=_text(raw.get("startedAt")),
                completed_at=_text(raw.get("completedAt")),
                source=_text(raw.get("source")) or "user",
                items=(),
            )

    durable_ids = {item.item_id for turn in turn_map.values() for item in turn.items}
    thread = _mapping(snapshot.get("thread"))
    current_turn_id = _text(thread.get("currentTurnId"))
    for record in live_records:
        mapped = item_from_record(record)
        if mapped is None or mapped.item_id in durable_ids:
            continue
        turn_id = mapped.turn_id or current_turn_id or "live"
        if turn_id not in turn_map:
            order.append(turn_id)
            turn_map[turn_id] = FlowTurn(
                turn_id,
                "running",
                mapped.created_at,
                "",
                "user",
                (),
            )
        current = turn_map[turn_id]
        turn_map[turn_id] = replace(
            current,
            items=_sort_items((*current.items, mapped)),
        )

    events = [
        event
        for event in (snapshot.get("events") or [])
        if isinstance(event, Mapping)
    ]
    for event in events:
        mapped = _context_item(event)
        if mapped is None:
            continue
        turn_id = mapped.turn_id
        if turn_id and turn_id in turn_map:
            current = turn_map[turn_id]
            if mapped.item_id not in {item.item_id for item in current.items}:
                turn_map[turn_id] = replace(
                    current,
                    items=_sort_items((*current.items, mapped)),
                )
            continue
        synthetic_id = f"system:{mapped.item_id}"
        order.append(synthetic_id)
        turn_map[synthetic_id] = FlowTurn(
            turn_id=synthetic_id,
            status="completed",
            started_at=mapped.created_at,
            completed_at=mapped.updated_at,
            source="system",
            items=(mapped,),
        )

    has_chat = any(
        item.kind in {"user", "assistant"}
        for turn in turn_map.values()
        for item in turn.items
    )
    messages = [
        message
        for message in (snapshot.get("messages") or [])
        if isinstance(message, Mapping)
    ]
    if messages and not has_chat:
        if not order:
            order.append("history")
            turn_map["history"] = FlowTurn(
                "history",
                "completed",
                "",
                "",
                "user",
                (),
            )
        first_id, last_id = order[0], order[-1]
        users: list[FlowItem] = []
        assistants: list[FlowItem] = []
        for index, message in enumerate(messages):
            role = _text(message.get("role"))
            content = _text(message.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            item = FlowItem(
                item_id=f"legacy-message:{index}",
                turn_id=first_id if role == "user" else last_id,
                kind=role,
                status="completed",
                created_at="",
                updated_at="",
                title="",
                text=content,
                tone="accent" if role == "assistant" else "user",
            )
            (users if role == "user" else assistants).append(item)
        if users:
            current = turn_map[first_id]
            turn_map[first_id] = replace(current, items=tuple(users) + current.items)
        if assistants:
            current = turn_map[last_id]
            turn_map[last_id] = replace(
                current,
                items=current.items + tuple(assistants),
            )

    target_turn_id = (
        current_turn_id
        if current_turn_id in turn_map
        else (order[-1] if order else "live")
    )
    if optimistic_user:
        if target_turn_id not in turn_map:
            order.append(target_turn_id)
            turn_map[target_turn_id] = FlowTurn(
                target_turn_id,
                "running",
                "",
                "",
                "user",
                (),
            )
        current = turn_map[target_turn_id]
        user_item = FlowItem(
            item_id="optimistic:user",
            turn_id=target_turn_id,
            kind="user",
            status="streaming",
            created_at="",
            updated_at="",
            title="",
            text=optimistic_user,
            tone="user",
            streaming=True,
        )
        turn_map[target_turn_id] = replace(
            current,
            items=(user_item, *current.items),
        )

    if live_assistant:
        if target_turn_id not in turn_map:
            order.append(target_turn_id)
            turn_map[target_turn_id] = FlowTurn(
                target_turn_id,
                "running",
                "",
                "",
                "user",
                (),
            )
        current = turn_map[target_turn_id]
        live_chat = [
            FlowItem(
                item_id=f"live-assistant:{item_id}",
                turn_id=target_turn_id,
                kind="assistant",
                status="streaming",
                created_at="",
                updated_at="",
                title="",
                text=text,
                tone="accent",
                streaming=True,
            )
            for item_id, text in live_assistant.items()
            if text
        ]
        if live_chat:
            turn_map[target_turn_id] = replace(
                current,
                items=current.items + tuple(live_chat),
            )

    unique_order: list[str] = []
    seen: set[str] = set()
    for turn_id in order:
        if turn_id not in seen and turn_id in turn_map:
            unique_order.append(turn_id)
            seen.add(turn_id)

    # Standalone system sections carry timestamps and should appear where they occurred.
    decorated = list(enumerate(unique_order))
    decorated.sort(
        key=lambda pair: (
            _parse_time(turn_map[pair[1]].started_at)
            or datetime.max.replace(tzinfo=timezone.utc),
            pair[0],
        )
    )
    return tuple(turn_map[turn_id] for _, turn_id in decorated)


__all__ = [
    "FlowItem",
    "FlowTurn",
    "build_message_flow",
    "elapsed_label",
    "item_from_record",
]
