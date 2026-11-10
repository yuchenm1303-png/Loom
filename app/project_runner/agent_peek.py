"""A compact look at what a conversation's sub-agents are doing and have done.

The hub and the phone list a conversation's sub-agents (a long goal's lines, its checker, investigations). A
card is a headline; this is the step behind it: a few recent rows of what the agent said and ran, not the
full transcript a main agent shows. ``peek`` reads one agent; ``history`` lists every sub-agent the
conversation ever had, because the Host closes an agent the moment it reports and the graph keeps it.
"""
from __future__ import annotations

import time

from app.agent_runtime.contracts import AgentEventKind as E

from .goal_common import charged

ROWS = 24
HISTORY_PAGE = 40
RESULT_CHARS = 2000
SAID_CHARS = 160
_KINDS = (E.MODEL_RESPONSE, E.TOOL_REQUESTED, E.TOOL_COMPLETED, E.TOOL_FAILED)
_OUTCOME = {"completed": "done", "failed": "failed"}


def descendants(control, thread_id):
    """Session ids of everything below the conversation, closed or not."""
    nodes = control.graph.list_tree(thread_id, include_closed=True)
    found = {thread_id}
    while True:
        before = len(found)
        found.update(node.session_id for node in nodes if node.parent_session_id in found)
        if len(found) == before:
            return found - {thread_id}, nodes


def _outcome(status):
    """How an agent ended, "" while it has not."""
    if status in {"running", "waiting_approval"}:
        return ""
    return _OUTCOME.get(status, "stopped")


def _describe(tool, arguments, fallback):
    """A tool call as one short line: a command or query in its own words, else what the project view says."""
    for key in ("cmd", "command", "query", "url"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return f"{tool} {' '.join(value.split())}"[:120]
    return fallback(tool, arguments)


def _rows(service, session_id, turn_id, describe):
    """The agent's latest steps, oldest first: what it said and what it ran, each with how it ended.

    The current turn only, from the raw log: the context projection drops what a model said, and a
    long session's whole log is megabytes that a refresh every second or two must not read."""
    events = service.store.turn_events(session_id, turn_id) if turn_id else ()
    rows, by_call = [], {}
    for event in events:
        if event.kind not in _KINDS:
            continue
        data = event.data or {}
        if event.kind is E.MODEL_RESPONSE:
            said = " ".join(str(data.get("text") or "").split())
            if said:
                rows.append({"kind": "say", "text": said[:SAID_CHARS], "at": event.created_at})
        elif event.kind is E.TOOL_REQUESTED:
            row = {"kind": "tool", "text": _describe(data.get("tool"), data.get("arguments") or {}, describe),
                   "at": event.created_at, "running": True, "ok": None}
            by_call[data.get("call_id")] = row
            rows.append(row)
        else:
            row = by_call.get(data.get("call_id"))
            if row is not None:
                row.update(running=False, ok=bool(data.get("ok")) and event.kind is E.TOOL_COMPLETED)
    return rows[-ROWS:]


def peek(service, params):
    """One sub-agent of this conversation: its state, its latest steps and, once it has ended, what it handed back."""
    thread_id = service._required_text(params, "threadId")
    agent_id = service._required_text(params, "agentId")
    control = service.runtime.agent_control
    below, nodes = descendants(control, thread_id)
    if agent_id not in below:
        raise ValueError("That agent does not belong to this conversation")
    node = next(item for item in nodes if item.session_id == agent_id)
    snapshot = control.snapshot(thread_id, agent_id)
    session = service.store.load(agent_id)
    closed = node.relation_status.value == "closed"
    status = snapshot.session_status.value
    from .service import _describe_tool, _result_text
    return {"id": agent_id, "role": node.role, "state": status, "executionRunning": snapshot.execution_running,
            "finished": closed or (not snapshot.execution_running and status not in {"running", "waiting_approval"}),
            "outcome": _outcome(status), "startedAt": node.created_at, "endedAt": node.updated_at if closed else None,
            "tokens": int(charged(session.usage)), "error": (snapshot.error or "")[:500],
            "rows": _rows(service, agent_id, session.current_turn_id, _describe_tool),
            "result": _result_text(node.role, snapshot.final_text)[:RESULT_CHARS] if snapshot.final_text else "",
            "observedAt": time.time()}


def _task_of(session):
    """What the agent was asked to do, in a few words: a line's title, else the start of its first message."""
    for message in session.messages:
        text = message.content if isinstance(message.content, str) else ""
        if not text.strip():
            continue
        for row in text.splitlines():
            if row.startswith("Your line:"):
                return row[len("Your line:"):].strip()[:80]
        first = next((row.strip() for row in text.splitlines() if row.strip()), "")
        return first[:80]
    return ""


def history(service, params):
    """Every sub-agent this conversation has had, newest first, one row each (all goals, not only the current)."""
    thread_id = service._required_text(params, "threadId")
    control = service.runtime.agent_control
    below, nodes = descendants(control, thread_id)
    cache = service.__dict__.setdefault("_agent_history_rows", {})
    rows = []
    for node in sorted((item for item in nodes if item.session_id in below), key=lambda item: item.created_at, reverse=True):
        closed = node.relation_status.value == "closed"
        cached = cache.get(node.session_id)
        if closed and cached and cached["endedAt"] == node.updated_at:
            rows.append(cached)
            continue
        snapshot = control.snapshot(thread_id, node.session_id)
        session = service.store.load(node.session_id)
        from .service import _result_text
        row = {"id": node.session_id, "parentId": node.parent_session_id, "role": node.role, "task": _task_of(session),
               "state": snapshot.session_status.value, "outcome": _outcome(snapshot.session_status.value),
               "finished": closed or not snapshot.execution_running, "startedAt": node.created_at,
               "endedAt": node.updated_at if closed else None, "tokens": int(charged(session.usage)),
               "result": _result_text(node.role, snapshot.final_text)[:240], "error": (snapshot.error or "")[:240]}
        if closed:
            cache[node.session_id] = row  # a closed agent no longer changes: its row is read once
        rows.append(row)
    start = max(0, int(params.get("offset") or 0))
    return {"agents": rows[start:start + HISTORY_PAGE], "total": len(rows), "observedAt": time.time()}
