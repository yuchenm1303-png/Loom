"""A long goal: one conversation keeps working on an objective until it is really done.

A project needs a Git repository, plans tasks and reviews each of them; a goal asked for
overnight in an empty folder needs none of that. It needs something to keep the agent going
and something to doubt its "done". So the agent calls weave_goal in its own conversation,
and the scheduler then brings the conversation back after each turn until the agent marks
the goal complete, the goal stops for a reason it records, or the budget is used up.

"Complete" is only a claim: an independent agent with none of the conversation's history
checks it against the objective, and what it finds goes back to the work.

The budget counts what a run costs, not what it re-reads: cached input at a tenth, because
one long conversation re-sends its whole history on every step (an overnight turn of 28M
tokens was 98% cached, and 8M of raw tokens would have stopped it after 40 minutes).
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from app.agent_runtime.contracts import AgentStatus
from app.agent_runtime.durable_state import GoalStatus

GOALS_FILE = "weave-goals.json"
CACHED_WEIGHT = 0.1
MAX_TURNS = 300
MAX_CHECKS = 3  # rejected checks before the person decides
MAX_CHECK_FAULTS = 2  # a checker that died or gave no verdict is run again, not believed
MAX_FAILURES = 8
MAX_STALLS = 2  # goal turns in a row without a tool call
RETRY_BASE, RETRY_MAX = 60, 1800
USAGE_EVERY, CHECK_EVERY = 30, 5
_BUSY = {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}

CHECK_PROMPT = (
    "You are an independent checker with none of the conversation that did this work. Another agent claims the "
    "objective below is finished. Do not trust its report or its files' descriptions of themselves: read what is in "
    "this workspace and run what can be run, so that each thing you accept is something you saw. Judge whether the "
    "objective is met in substance, not whether files exist. Do not edit, fix or delete anything in the workspace.\n"
    'End with only JSON: {"verdict":"passed|needs_changes|blocked","summary":"what you checked and saw",'
    '"issues":["a concrete problem and what would settle it"]}. needs_changes requires issues; blocked means you '
    "could not check it here.\nObjective:\n"
)
CHECK_NOW = ("Your limit stopped you. Do not start anything new: give your verdict now from what you have already "
             "seen, as that JSON only, and say in the summary what you did not get to check.")


class Goals:
    """The driver's record of each conversation's goal, beside the project store; survives a Host restart."""

    def __init__(self, home):
        self.path = home / GOALS_FILE
        self.lock = threading.Lock()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def all(self):
        with self.lock:
            return self._read()

    def get(self, thread_id):
        return self.all().get(thread_id)

    def put(self, thread_id, record):
        with self.lock:
            value = self._read()
            value[thread_id] = record
            self._write(value)

    def take(self, thread_id):
        with self.lock:
            value = self._read()
            if value.pop(thread_id, None) is not None:
                self._write(value)


def charged(usage):
    """What a usage record costs against a goal's budget."""
    return (usage.total_tokens - usage.cached_input_tokens) + usage.cached_input_tokens * CACHED_WEIGHT


def _children(service, thread_id):
    control = getattr(service.runtime, "agent_control", None)
    return control.list_tree(thread_id) if control else ()


def _charge(service, thread_id, record):
    """Everything spent since the goal began, its sub-agents included."""
    session = service.store.load(thread_id)
    spent = charged(session.usage) - record["baseCharged"]
    known = record.get("baseChildren", [])
    for snapshot in _children(service, thread_id):
        if snapshot.node.session_id not in known:
            spent += charged(service.store.load(snapshot.node.session_id).usage)
    record["tokens"] = int(max(0, spent))
    return session


def start(service, thread_id, objective, token_budget):
    existing = service.runtime.get_goal(thread_id)
    if existing is not None and existing.status is GoalStatus.ACTIVE and service.weave_goals.get(thread_id):
        raise ValueError("A long goal is already running in this conversation; finish it or ask the person to stop it")
    session = service.store.load(thread_id)
    service.runtime.set_goal(thread_id, objective)
    service.weave_goals.put(thread_id, {
        "tokenBudget": token_budget, "tokens": 0, "baseCharged": charged(session.usage),
        "baseChildren": [item.node.session_id for item in _children(service, thread_id)],
        "phase": "working", "turns": 0, "stalls": 0, "failures": 0, "checks": 0, "checkFaults": 0, "retryAt": 0,
        "feedback": "", "checkerId": "", "reason": "", "summary": "", "checked": False,
        "goalTurn": "", "handledTurn": session.current_turn_id, "startedAt": time.time()})


def _stop(service, thread_id, record, status, reason):
    record["reason"] = reason[:1000]
    service.weave_goals.put(thread_id, record)
    service.runtime.set_goal_status(thread_id, status)
    return True


def _finish(service, thread_id, record, summary, checked):
    record.update(phase="done", summary=summary[:2000], checked=checked, reason="")
    service.weave_goals.put(thread_id, record)
    service.runtime.set_goal_status(thread_id, GoalStatus.COMPLETE)
    return True


def _spawn_checker(service, thread_id, record, objective):
    snapshot = service.runtime.agent_control.spawn(thread_id, CHECK_PROMPT + objective, role="goal-check",
                                                   history_mode="none", background=True)
    record.update(phase="checking", checkerId=snapshot.node.session_id, checkerPressed=False)
    service.weave_goals.put(thread_id, record)


def _verdict(text):
    """The last verdict in the reply: a real checker gave a short one, then the full one with its issues."""
    found, at, decoder = None, text.find("{"), json.JSONDecoder()
    while at >= 0:
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find("{", at + 1)
            continue
        if isinstance(value, dict) and value.get("verdict") in {"passed", "needs_changes", "blocked"}:
            found = value
        at = text.find("{", end)
    return found


def _collect(service, thread_id, record, objective):
    """Read the checker's verdict once it has finished."""
    control = service.runtime.agent_control
    snapshot = control.snapshot(thread_id, record["checkerId"])
    if snapshot.execution_running or snapshot.session_status in _BUSY:
        return False
    verdict = _verdict(snapshot.final_text or "") if snapshot.session_status is AgentStatus.COMPLETED else None
    if snapshot.session_status is AgentStatus.LIMIT_REACHED and not record.get("checkerPressed"):
        # What it has seen so far is worth more than a new checker starting from nothing.
        record["checkerPressed"] = True
        service.weave_goals.put(thread_id, record)
        control.send(thread_id, record["checkerId"], CHECK_NOW)
        return True
    _charge(service, thread_id, record)
    control.close_agent(thread_id, record["checkerId"])
    record["checkerId"] = ""
    if verdict is None or verdict["verdict"] == "blocked":
        record["checkFaults"] += 1
        if record["checkFaults"] < MAX_CHECK_FAULTS:
            _spawn_checker(service, thread_id, record, objective)
            return True
        return _finish(service, thread_id, record, str((verdict or {}).get("summary") or "The check could not be completed."), False)
    summary = str(verdict.get("summary") or "")
    if verdict["verdict"] == "passed":
        return _finish(service, thread_id, record, summary, True)
    issues = [str(item) for item in verdict.get("issues") or []]
    record["checks"] += 1
    record.update(phase="working", checkFaults=0, feedback=(summary + "\n" + "\n".join(f"- {item}" for item in issues))[:6000])
    if record["checks"] >= MAX_CHECKS:
        return _stop(service, thread_id, record, GoalStatus.BLOCKED,
                     f"An independent check rejected the work {record['checks']} times: {summary}")
    service.weave_goals.put(thread_id, record)
    return True


def _settle_turn(service, thread_id, record, session):
    """Account for the turn that just ended; returns True when the goal stopped because of it."""
    turn = session.current_turn_id
    if turn == record.get("handledTurn"):
        return False
    record["handledTurn"] = turn
    if session.status is AgentStatus.CANCELLED:
        return _stop(service, thread_id, record, GoalStatus.PAUSED, "Stopped by the person")
    if session.status is AgentStatus.FAILED:
        record["failures"] += 1
        if record["failures"] > MAX_FAILURES:
            return _stop(service, thread_id, record, GoalStatus.BLOCKED, str(session.error or "The turn failed"))
        record["retryAt"] = time.time() + min(RETRY_BASE * 2 ** (record["failures"] - 1), RETRY_MAX)
        return False
    record["failures"] = 0
    if turn == record.get("goalTurn"):
        record["stalls"] = 0 if session.tool_calls else record["stalls"] + 1
        if record["stalls"] >= MAX_STALLS:
            return _stop(service, thread_id, record, GoalStatus.BLOCKED,
                         f"{MAX_STALLS} turns in a row made no progress: {str(session.final_text or '')[:600]}")
    return False


def _continue(service, thread_id, record, session):
    if record["turns"] >= MAX_TURNS:
        return _stop(service, thread_id, record, GoalStatus.BLOCKED, f"Reached {MAX_TURNS} turns")
    note, turn_id = record["feedback"], str(uuid.uuid4())
    record.update(turns=record["turns"] + 1, feedback="", goalTurn=turn_id)
    service.weave_goals.put(thread_id, record)

    def run():
        service._prepare_turn(session)
        return service.runtime.continue_goal(thread_id, note=note, turn_id=turn_id)

    service._launch(thread_id, run)
    return True


def _advance(service, thread_id, record, polled):
    goal = service.runtime.get_goal(thread_id)
    if goal is None:
        service.weave_goals.take(thread_id)
        return False
    if goal.status not in {GoalStatus.ACTIVE, GoalStatus.COMPLETE} or record["phase"] == "done":
        return False
    now = time.time()
    if service._is_active(thread_id):
        if now - polled.get(thread_id, 0) < USAGE_EVERY:
            return False
    elif record["phase"] == "checking" and now - polled.get(thread_id, 0) < CHECK_EVERY:
        return False
    elif now < record["retryAt"]:
        return False
    polled[thread_id] = now
    session = _charge(service, thread_id, record)
    if record["tokens"] >= record["tokenBudget"]:
        _stop(service, thread_id, record, GoalStatus.BUDGET_LIMITED, "The token budget is used up")
        if session.status in _BUSY or service._is_active(thread_id):
            service.runtime.cancel(thread_id)
        return True
    service.weave_goals.put(thread_id, record)
    if service._is_active(thread_id) or session.status in _BUSY:
        return False
    if goal.status is GoalStatus.COMPLETE:  # the agent's claim, not yet a fact
        service.runtime.set_goal_status(thread_id, GoalStatus.ACTIVE)
        _spawn_checker(service, thread_id, record, goal.objective)
        return True
    if record["phase"] == "checking":
        return _collect(service, thread_id, record, goal.objective)
    if _settle_turn(service, thread_id, record, session):
        return True
    service.weave_goals.put(thread_id, record)
    if now < record["retryAt"]:
        return False
    return _continue(service, thread_id, record, session)


def advance(service):
    """One scheduler tick: bring each long goal one move forward; returns whether anything moved."""
    polled = service.__dict__.setdefault("_goal_polled", {})
    moved = False
    for thread_id, record in service.weave_goals.all().items():
        try:
            moved = _advance(service, thread_id, record, polled) or moved
        except (OSError, ValueError, RuntimeError) as exc:
            # A goal must never take the scheduler down; a session being written is read again next tick.
            service.__dict__.setdefault("_goal_errors", {})[thread_id] = f"{type(exc).__name__}: {exc}"
    return moved


def control(service, params):
    """The hub's stop, resume and dismiss."""
    thread_id = service._required_text(params, "threadId")
    action, record = params.get("action"), service.weave_goals.get(thread_id)
    if record is None or service.runtime.get_goal(thread_id) is None:
        raise ValueError("This conversation has no long goal")
    if action == "pause":
        _stop(service, thread_id, record, GoalStatus.PAUSED, "Stopped by the person")
        if service._is_active(thread_id):
            service.runtime.cancel(thread_id)
    elif action == "resume":
        if record["phase"] == "done":
            raise ValueError("This goal is finished")
        if record["tokens"] >= record["tokenBudget"]:
            record["tokenBudget"] += record["tokenBudget"]  # resuming a spent goal means spending more
        record.update(reason="", stalls=0, failures=0, retryAt=0, handledTurn=service.store.load(thread_id).current_turn_id)
        service.weave_goals.put(thread_id, record)
        service.runtime.set_goal_status(thread_id, GoalStatus.ACTIVE)
    elif action == "dismiss":
        service.runtime.clear_goal(thread_id)
        service.weave_goals.take(thread_id)
    else:
        raise ValueError("action must be pause, resume or dismiss")
    return {"goal": view(service, thread_id)}


def view(service, thread_id):
    record = service.weave_goals.get(thread_id)
    goal = service.runtime.get_goal(thread_id) if record else None
    if goal is None:
        return None
    return {"objective": goal.objective[:300], "status": goal.status.value, "phase": record["phase"],
            "tokens": record["tokens"], "tokenBudget": record["tokenBudget"], "turns": record["turns"],
            "reason": record["reason"], "summary": record["summary"], "checked": record["checked"]}
