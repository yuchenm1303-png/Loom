"""investigate: a read-only sub-agent that finds something out and hands back only its answer.

The most established use of sub-agents (Claude Code's Explore, Cognition's read-only helpers, Anthropic's research
workers): reading in a separate context. Everything a conversation reads stays in its history and is re-sent on
every later step, so a question answered by reading twenty files costs the asker those files on every step after.
Asked of an investigator, the reading is paid once and only the answer enters the asker's history.

Read-only is enforced, not requested: the child runs in the read-only permission mode (no writes, no commands; a
denied call is refused, never asked) and is offered only reading tools. It has no history and a short prompt of
its own. The call waits for the answer; several can run at once. Over its limits it is asked to answer from what
it has; when the asker's turn is cancelled, so is the investigation; it is always closed afterwards.

Models do not delegate on their own (30 days: 5 sub-agents in about 11,000 model calls that offered spawn_agent),
and a note did not change that: two real runs were told at their tenth read and read on. They were right to: both
cost about 130k in all, and in a small context reading yourself is cheaper than starting an investigator. It pays
when the asker's context is already large, since every read is then re-sent on all later steps. So the Host steps
in only there: once a turn has read HOLD_AFTER times and the conversation is past HOLD_CONTEXT tokens, it holds the
next batch of reads once, the way the task-list and lines gates do. Repeating the same calls runs them.

A free model (beam-free) held that way did investigate, twice: its own tokens fell 39%, but the investigators
read 46 and 60 times and everything together cost three times as much, for an answer no better. An investigation
buys a cleaner history, which pays only over many later steps, so the hold applies only during a long goal.
"""
from __future__ import annotations

import re
import threading
import time

from .contracts import AgentEventKind, ToolEffect
from .tools import AgentTool, ToolResult

ROLE = "investigator"
READ_TOOLS = ("list_workspace_files", "read_workspace_text", "search_workspace_text", "search_memory", "read_memory",
              "web_search")
WORKSPACE_READS = frozenset({"list_workspace_files", "read_workspace_text", "search_workspace_text"})
MIN_QUESTION = 20
ANSWER_CHARS = 4000
TOKEN_CAP = 200_000  # weighted; a real investigator reached 384k for an answer the asker got for about 260k itself
TIME_CAP = 600  # seconds
ANSWER_GRACE = 120  # seconds to answer after being asked to
POLL = 2.0
HOLD_AFTER = 10  # reads in one turn of a top-level conversation before the Host may step in
HOLD_CONTEXT = 60_000  # estimated tokens of the conversation; below it, reading yourself is the cheaper way

SYSTEM = (
    "You are a read-only investigator for another agent working in this folder. You can list, read and search files "
    "(and search the web or memory when offered); you cannot change anything or run commands, and asking is not "
    "possible. Find out what you were asked, as directly as you can: search before reading whole files, read only "
    "what answers the question, stop as soon as you can answer.\n"
    "Your answer is all the other agent sees of your work. Give the answer first, then the evidence it rests on as "
    "file:line references with the few lines that matter, and say plainly what you could not find or did not check. "
    "At most 400 words; no plan, no narration of what you did.")
TASK = "Question:\n{question}\n{scope}"
SCOPE = "Start with: {paths}\n"
ANSWER_NOW = ("Your limit for this investigation is reached. Do not read anything more: answer now from what you have, "
              "and say what you did not get to.")
HELD = ("Not executed, once: this conversation is already about {tokens:,} tokens and this turn has read {count} times. "
        "Everything you read now is re-sent on every later step. If what you still need is to find something out "
        "(where something is handled, how a part works, what calls what), call investigate with the question: it "
        "reads in its own context and returns only the answer, and several can run at once. If you need these exact "
        "contents yourself, for instance to change them, repeat the same calls and they run.")


def _charged(usage):
    from app.project_runner.goal_common import charged
    return charged(usage)


def investigate_tool(control) -> AgentTool:
    runtime = control.runtime

    def handler(context, arguments):
        question = " ".join(str(arguments.get("question") or "").split())
        if len(question) < MIN_QUESTION:
            raise ValueError("Ask a complete question: what to find out, and what the answer is for.")
        node = control.graph.get(context.session_id)
        if node is not None and node.role == ROLE:
            raise ValueError("An investigator answers by itself; it cannot start another.")
        paths = [str(path).strip() for path in arguments.get("paths") or () if str(path).strip()][:20]
        task = TASK.format(question=question, scope=SCOPE.format(paths=", ".join(paths)) if paths else "")
        child = control.spawn(context.session_id, task, role=ROLE, history_mode="none", background=True,
                              read_only=True, tool_selection=list(READ_TOOLS), system_prompt=SYSTEM).node.session_id
        try:
            return _await(control, runtime, context, child)
        finally:
            try:
                control.close_agent(context.session_id, child)
            except (OSError, ValueError, RuntimeError):
                pass

    return AgentTool(
        name="investigate",
        description=(
            "Find something out without reading it into this conversation: a read-only sub-agent lists, searches and "
            "reads files in its own context and returns only its answer with file:line evidence. Use it for questions "
            "that take reading many files (where is X handled, how does Y work, what calls Z, does anything already "
            "do W). Read yourself what you are about to change. Several investigate calls can run at once."),
        input_schema={"type": "object", "properties": {
            "question": {"type": "string", "description": "What to find out and what the answer is for; the "
                         "investigator sees none of this conversation."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Where to start, if you know."},
        }, "required": ["question"], "additionalProperties": False},
        handler=handler,
        effect=ToolEffect.READ_ONLY,
        supports_parallel_tool_calls=True,
    )


def _await(control, runtime, context, child):
    started, asked = time.time(), 0.0
    while True:
        snapshot = control.wait(context.session_id, child, timeout_seconds=POLL)
        if not snapshot.execution_running:
            break
        if context.cancelled:
            runtime.cancel(child)
            raise RuntimeError("agent turn cancellation requested")
        now = time.time()
        if asked:
            if now - asked > ANSWER_GRACE:
                runtime.cancel(child)
                break
            continue
        if now - started > TIME_CAP or _charged(runtime.store.load(child).usage) >= TOKEN_CAP:
            runtime.cancel(child)
            control.wait(context.session_id, child, timeout_seconds=30)
            control.send(context.session_id, child, ANSWER_NOW)
            asked = time.time()
    session = runtime.store.load(child)
    answer = str(session.final_text or "").strip()
    reads = sum(1 for event in runtime.store.events(child)
                if event.kind is AgentEventKind.TOOL_COMPLETED and event.data.get("tool") in WORKSPACE_READS)
    spent = int(_charged(session.usage))
    if not answer:
        why = session.error or "it ended without an answer"
        return ToolResult(False, f"The investigation gave no answer ({why}). Investigate again more narrowly, or "
                          "read what you need yourself.", {"agent_id": child, "reads": reads, "tokens": spent})
    if len(answer) > ANSWER_CHARS:
        answer = answer[:ANSWER_CHARS] + "\n(cut)"
    return ToolResult(True, f"Investigator's answer ({reads} reads, about {spent:,} tokens, none of it in your "
                      f"history):\n{answer}", {"agent_id": child, "reads": reads, "tokens": spent})


_READ_WORDS = re.compile(r"(?i)\b(get-content|select-string|get-childitem|gc|cat|type|more|head|tail|rg|grep|findstr|"
                         r"ls|dir|tree|find|wc|git\s+(show|log|grep|diff|blame|ls-files))\b")
_WRITE_WORDS = re.compile(r"(?i)(>|\b(set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|"
                          r"rename-item|sed\s+-i|git\s+(commit|add|checkout|reset|apply|stash|merge|rebase|push))\b|"
                          r"(^|[\s;|&(])(del|rm|mv|cp|mkdir|python3?|py|node|npm|npx|pip)(\.exe)?(\s|$))")
_counts: dict = {}
_counts_lock = threading.Lock()


def _is_read(call):
    """Reading, for the hold only: a misjudged command moves a one-time hold a little, nothing else."""
    if call.name in WORKSPACE_READS:
        return True
    if call.name != "exec":
        return False
    argv = call.arguments.get("argv")
    line = " ".join(str(part) for part in argv) if isinstance(argv, (list, tuple)) else str(argv or "")
    return bool(_READ_WORDS.search(line)) and not _WRITE_WORDS.search(line)


def _top_level(runtime, session):
    graph = getattr(runtime, "agent_graph", None)
    return graph is None or graph.get(session.session_id) is None  # sub-agents do their own reading


def _state(session):
    """(reads this turn, settled: it investigated or was held already). Caller holds the lock."""
    turn, count, settled = _counts.get(session.session_id, ("", 0, False))
    return (count, settled) if turn == session.current_turn_id else (0, False)


def annotate(runtime, session, call, result):
    """Counts this turn's reads, as results come in; the result itself is unchanged.

    In memory per conversation and turn: the event view keeps no command lines, and counting must not cost a scan
    of a long log. A restart only starts the count again."""
    if not result.ok or runtime.tools.get("investigate") is None or not _top_level(runtime, session):
        return result
    asked = call.name == "investigate"
    if asked or _is_read(call):
        with _counts_lock:
            count, settled = _state(session)
            _counts[session.session_id] = (session.current_turn_id, count + (0 if asked else 1), settled or asked)
    return result


def _in_long_goal(runtime, session):
    get_goal = getattr(runtime, "get_goal", None)
    goal = get_goal(session.session_id) if get_goal else None
    return goal is not None and getattr(goal.status, "value", goal.status) == "active"


def hold(runtime, session, step, calls):
    """Before a batch runs: hold its reads once, in a long goal, when the turn has read a lot in a conversation
    already large. Only there: an investigation costs more in all than reading yourself (a real one-question
    run: the asker spent 39% less, everything together three times as much), and pays only when the asker goes on
    for many more steps, each re-sending what it did not read."""
    if step.tool_router.get("investigate") is None or not _top_level(runtime, session):
        return None
    if not _in_long_goal(runtime, session):
        return None
    reads = tuple(call for call in calls if _is_read(call))
    if not reads:
        return None
    with _counts_lock:
        count, settled = _state(session)
        if settled or count < HOLD_AFTER:
            return None
        from .context_budget import estimate_tokens
        tokens = estimate_tokens(session.messages)
        if tokens < HOLD_CONTEXT:
            return None
        _counts[session.session_id] = (session.current_turn_id, count, True)
    refusal = ToolResult(False, HELD.format(tokens=tokens, count=count),
                         {"failure_kind": "investigate_suggested", "execution_status": "not_executed",
                          "retry_policy": "investigate_or_repeat"})
    return tuple((call, refusal) for call in reads)


__all__ = ["investigate_tool", "annotate", "hold", "ROLE"]
