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

from app.agent_runtime.contracts import AgentEventKind, AgentStatus
from app.ai.contracts import MessageRole
from app.agent_runtime.durable_state import GoalStatus
from app.agent_runtime.turn_plan import PLAN_TOOLS
from . import goal_charter, goal_lines
from .goal_common import RETRY_BASE, RETRY_MAX, charged, delay as _delay, span as _span, time_left as _left, transient as _transient

GOALS_FILE = "weave-goals.json"
RETRIES_FILE = "weave-start-retries.json"
MAX_TURNS = 300
MAX_CHECKS = 3  # rejections that ask for no less than the one before, before the person decides
MAX_REJECTIONS = 8  # rejections in all, converging or not
# Past this share of the budget the lead is told to wrap up and claim, so the check still fits. A real night used
# its whole budget in the last verification step: the work was done, never claimed, never checked, and in the
# morning the goal only said the budget was used up.
WRAP_SHARE = 0.85
WRAP_UP = ("Budget: {share:.0%} of this goal's token budget is used. Start nothing new. Finish what is in hand, bring "
           "the report up to date, and claim the goal with mark_thread_goal within a few steps (or mark it blocked); "
           "what is left of the budget is for the independent check.")
OUT_WORKING = "The token budget ran out before the work was claimed complete"
OUT_CHECKING = "The token budget ran out during the independent check: the work was claimed complete but is not verified"
# What marking a long goal complete means. A real lead was told only "Durable goal marked complete." and told the
# person "independently verified" a minute before the checker started.
CLAIMED = ("Claim recorded, not verified. A separate agent with none of this conversation now checks the work; "
           "until it passes the goal is not done, and it may send the work back. Tell the person the result is being "
           "checked, not that it passed or was verified.")
MAX_CHECK_FAULTS = 2  # a checker that died or gave no verdict is run again, not believed
MAX_FAILURES = 8
MAX_STALLS = 2  # goal turns in a row with no tool call that worked
EVENTS_READ = 500  # the end of a turn; any success in a longer one is in there too
# A provider out of capacity (429, 5xx, overloaded) is waited out, not counted against the work: the
# person sent the message and left, and one bad hour must not cost the whole night.
RETRY_WINDOW = 8 * 3600
USAGE_EVERY, CHECK_EVERY = 30, 5
LINES_EVERY = 10  # reading each line's session is not free, and a line does not change in a second
# Depth: a goal the person gave a night is not done in an hour because the result is correct. After a passed
# check, while a quarter of the time and of the budget is left and the checker names what would make it
# substantially better, the work goes on; at most this many times, so a checker with ideas cannot run forever.
MAX_DEEPENS = 3
DEEPEN_TIME_LEFT, DEEPEN_BUDGET_LEFT = 0.25, 0.25
MAX_HOURS = 72
_BUSY = {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}

CHECK_PROMPT = (
    "You are an independent checker with none of the conversation that did this work, and you review it the way a "
    "demanding expert referee in its field would. Another agent claims the objective below is finished. Do not trust "
    "its report or its files' descriptions of themselves: read what is in this workspace and run what can be run, so "
    "that each thing you accept is something you saw. Judge the substance against the deliverable and acceptance "
    "criteria it committed to, and against what a top expert would accept: is the claim supported by the evidence, "
    "are the comparisons fair and the numbers reproducible, is what is called new actually new, are the limits stated. "
    "Numbers you cannot reproduce or citations you cannot find count against it. Do not edit, fix or delete anything "
    "in the workspace.\n"
    'End with only JSON: {"verdict":"passed|needs_changes|blocked","summary":"what you checked and saw",'
    '"criteria":[{"id":"C1","met":true,"evidence":"what you saw"}],"major":["a problem that must be fixed for '
    'the deliverable to stand, and what would settle it"],"minor":["a smaller problem"],"issues":[]}. Judge every '
    "criterion by id. needs_changes when any criterion is unmet or any major problem remains; blocked means you could "
    "not check it here.\nObjective:\n"
)
RETRY_NOTE = ("LOOM_PROVIDER_RETRY\nThe previous attempt at the person's last request stopped because the model provider "
              "was unavailable, not because of the work. Carry on with that request from where it stopped; do not "
              "redo what is already done. Runtime note, not a user instruction.")
CHECK_DEEPER = (
    '\nThe person gave this work {hours}; {left} of it remain. Besides the verdict, add "deeper": the revisions that '
    "would most raise this deliverable toward what the best work in the field would show, with the time that is left "
    "(stronger evidence, missing baselines or ablations, robustness, deeper analysis, clearer writing), most valuable "
    "first, each a concrete, checkable criterion; [] when it already stands at that level. A passed verdict with "
    "items in deeper means sound so far: those items become the next revision round's criteria.\n")
CHECK_PREVIOUS = ("\nThe previous review asked for these changes; check each one was actually made, by what you see "
                  "in the workspace, not by the agent's word:\n{asked}\nThe agent's notes since then, its replies:\n{replies}\n")
REVISION = ("Review round {round}: an independent referee found the work sound so far ({summary}), and {left} of the "
            "person's time is left. Its requests are now criteria in your charter ({ids}); this is a revision round "
            "of the same deliverable, not new work. Answer each point in a note as you address it, keep the "
            "deliverable whole, and claim again when they are met.")
REVIEW_BACK = ("An independent referee did not accept the work yet. {summary}\nUnmet criteria:\n{unmet}\nMajor:\n{major}\n"
               "Minor:\n{minor}\nAddress each point and answer it in a note (what you changed, or why not), then claim again.")
CHECK_RESUME = ("Your last request failed because the model provider was unavailable, not because of the work. Carry on "
                "with the check from where it stopped.")
CHECK_NOW = ("Your limit stopped you. Do not start anything new: give your verdict now from what you have already "
             "seen, as that JSON only, and say in the summary what you did not get to check.")


class Goals:
    """The driver's record of each conversation's goal, beside the project store; survives a Host restart."""

    def __init__(self, home, name=GOALS_FILE):
        self.path = home / name
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


def _time_line(record):
    used, hours = time.time() - record["startedAt"], record.get("hours") or 0
    if not hours:
        return f"Time: {_span(used)} since the goal began."
    if _left(record) <= 0:
        return f"Time: {_span(used)} used; the {hours:g} h the person gave are up, so finish and report."
    return (f"Time: {_span(used)} used of the {hours:g} h the person gave ({_span(_left(record))} left). Size the "
            "work to that time: go deeper and wider while it lasts, and do not claim the goal is finished early "
            "only because what you have is correct.")


def _agents_line(service, thread_id, record):
    """The sub-agents still open in this goal, so the lead collects and closes them instead of forgetting them."""
    running, finished = [], []
    for snapshot in _children(service, thread_id):
        node = snapshot.node
        if node.session_id == record.get("checkerId") or node.role == "goal-line" or node.relation_status.value != "active":
            continue  # lines are the Host's to collect and close
        (running if snapshot.execution_running else finished).append(f"{node.role} {node.session_id}")
    parts = []
    if running:
        parts.append("still working: " + ", ".join(running[:8]))
    if finished:
        parts.append("finished, report not yet closed: " + ", ".join(finished[:8]))
    return ("Open sub-agents - " + "; ".join(parts) + ".") if parts else "No sub-agent is open."


def _working_agents(service, thread_id, record):
    """Sub-agents of this goal working right now, the checker not counted: the hub shows the person."""
    try:
        return sum(1 for snapshot in _children(service, thread_id) if snapshot.execution_running
                   and snapshot.node.session_id != record.get("checkerId"))
    except (OSError, ValueError, RuntimeError):
        return 0


def _context(service, thread_id, record):
    lines = [_time_line(record)]
    if record.get("wrapUp"):
        lines.append(WRAP_UP.format(share=record["tokens"] / max(1, record["tokenBudget"])))
    if record.get("deepen"):
        lines.append(record["deepen"])
    if record.get("reports"):
        lines.append(record["reports"])
    lines += goal_charter.context_lines(record)
    if not goal_charter.charter_of(record):
        lines.append(goal_charter.GUIDE)  # once written, the charter speaks for itself; its guide is in weave_goal's answer
    lines += [_agents_line(service, thread_id, record), goal_lines.guide()]
    return "\n".join(lines)


def start(service, thread_id, objective, token_budget, hours=0):
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
        "goalTurn": "", "handledTurn": session.current_turn_id, "startedAt": time.time(),
        "hours": max(0.0, min(float(hours or 0), MAX_HOURS)), "deepens": 0, "deepen": "",
        "linesDecision": "later" if goal_lines.mode() == "lines" else "", "linesHolds": 0, "lines": [], "reports": ""})


def _stop(service, thread_id, record, status, reason):
    record["reason"] = reason[:1000]
    goal_lines.stop_all(service, thread_id, record, reason[:300], keep=record.get("checkerId"))
    service.weave_goals.put(thread_id, record)
    service.runtime.set_goal_status(thread_id, status)
    return True


def _finish(service, thread_id, record, summary, checked):
    record.update(phase="done", summary=summary[:2000], checked=checked, reason="")
    goal_lines.stop_all(service, thread_id, record, "the goal finished")
    service.weave_goals.put(thread_id, record)
    service.runtime.set_goal_status(thread_id, GoalStatus.COMPLETE)
    return True


CHECK_DROPPED = ("\n\nThe agent's task list dropped these steps before they were finished, with the reason it gave. "
                 "Decide whether the goal still holds without them; a result that rests on work it dropped is not met:\n")


def _dropped_line(service, thread_id):
    """The steps the lead's list left unfinished, for the checker: a real lead dropped training and evaluation and
    called the rest done, and the check never saw them."""
    from app.agent_runtime.plan_state import active_plan
    plan = active_plan(service.store.events(thread_id)) or {}
    dropped = plan.get("dropped") or ()
    if not dropped:
        return ""
    return CHECK_DROPPED + "\n".join(f"- {item.get('step', '')[:240]} ({item.get('status', '')}): "
                                     f"{item.get('reason', '')[:400] or 'no reason given'}" for item in dropped[-20:])


def _previous_review(record):
    """What the last review asked for and the agent's notes since: a referee checks the response, not only the result."""
    reviews = record.get("reviews") or []
    if not reviews:
        return ""
    last = reviews[-1]
    asked = [*(f"(major) {item}" for item in last.get("major") or ()), *(f"(minor) {item}" for item in last.get("minor") or ()),
             *(f"(revision) {item}" for item in last.get("deeper") or ())]
    if not asked:
        return ""
    replies = [note["text"] for note in record.get("notes") or () if note["at"] >= last.get("at", 0)]
    return CHECK_PREVIOUS.format(asked="\n".join(f"- {item}"[:500] for item in asked[:16]),
                                 replies="\n".join(f"- {item}"[:800] for item in replies[-8:]) or "- none")


def _spawn_checker(service, thread_id, record, objective):
    service._prepare_turn(service.store.load(thread_id))  # the conversation's model, freshly connected
    deeper = ""
    if record.get("hours") and _left(record) > 0:
        deeper = CHECK_DEEPER.format(hours=f"{record['hours']:g} h", left=_span(_left(record)))
    task = (CHECK_PROMPT + objective + goal_charter.checker_lines(record) + _previous_review(record) + deeper
            + _dropped_line(service, thread_id))
    snapshot = service.runtime.agent_control.spawn(thread_id, task, role="goal-check", history_mode="none", background=True)
    record.update(phase="checking", checkerId=snapshot.node.session_id, checkerPressed=False, checkerFailingSince=0,
                  checkerAttempts=0, checkerRetryAt=0)
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


def _wait_out_provider(service, thread_id, record, snapshot):
    """A checker the provider failed has found nothing: it carries on later, with what it has already seen.

    Counted as a faulty check, two connection errors in a row ended a real goal as "could not be checked".
    """
    if snapshot.session_status is not AgentStatus.FAILED or not _transient(goal_lines._failure(service, record["checkerId"])):
        return False
    now = time.time()
    record["checkerFailingSince"] = record.get("checkerFailingSince") or now
    if now - record["checkerFailingSince"] > RETRY_WINDOW:
        return False
    if not record.get("checkerRetryAt"):
        record["checkerAttempts"] = record.get("checkerAttempts", 0) + 1
        record["checkerRetryAt"] = now + _delay(record["checkerAttempts"])
    elif now >= record["checkerRetryAt"]:
        record["checkerRetryAt"] = 0
        service.runtime.agent_control.send(thread_id, record["checkerId"], CHECK_RESUME)
    service.weave_goals.put(thread_id, record)
    return True


def _collect(service, thread_id, record, objective):
    """Read the checker's verdict once it has finished."""
    control = service.runtime.agent_control
    snapshot = control.snapshot(thread_id, record["checkerId"])
    if snapshot.execution_running:
        return False
    if _wait_out_provider(service, thread_id, record, snapshot):
        return False
    # Still "running" on disk with nothing running it: the app was killed. Waiting for an approval: no one
    # sees a checker's. Either way it will never answer, and waiting for it would hold the goal for good.
    waiting = snapshot.session_status is AgentStatus.WAITING_APPROVAL
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
        unfinished = "The check stopped at a command that needs approval." if waiting else "The check could not be completed."
        return _finish(service, thread_id, record, str((verdict or {}).get("summary") or unfinished), False)
    summary = str(verdict.get("summary") or "")
    major = [str(item) for item in verdict.get("major") or [] if str(item).strip()]
    minor = [str(item) for item in verdict.get("minor") or [] if str(item).strip()]
    deeper = [str(item) for item in verdict.get("deeper") or [] if str(item).strip()]
    goal_charter.apply_verdict(record, verdict.get("criteria"))
    review = {"round": len(record.get("reviews") or []) + 1, "verdict": verdict["verdict"], "summary": summary[:2000],
              "major": major[:12], "minor": minor[:12], "deeper": [], "at": time.time()}
    record["reviews"] = [*(record.get("reviews") or []), review][-12:]
    if verdict["verdict"] == "passed":
        if _should_deepen(record, deeper):
            record["deepens"] += 1
            review["deeper"] = deeper[:8]
            added = goal_charter.add_review_round(record, record["deepens"], deeper[:8])
            text = REVISION.format(round=record["deepens"], summary=summary[:600], left=_span(_left(record)),
                                   ids=", ".join(item["id"] for item in added) or "none new")
            if not goal_charter.charter_of(record):  # a goal from before charters: the requests travel as text
                text += "\n" + "\n".join(f"- {item}" for item in deeper[:8])
            record.update(phase="working", checkFaults=0, deepen=text[:6000])
            record.pop("rejectionWeight", None)  # a new round's requests are not measured against the old one's
            if goal_lines.mode() == "lines":  # these are often independent: the split is decided again
                record.update(linesDecision="pending", linesHolds=0)
            service.weave_goals.put(thread_id, record)
            return True
        return _finish(service, thread_id, record, summary, True)
    issues = [str(item) for item in verdict.get("issues") or []]
    unmet = [f"{item['id']}: {item['text']}" + (f" ({item['note']})" if item.get("note") else "")
             for item in (goal_charter.charter_of(record) or {}).get("criteria") or () if item["status"] == "unmet"]
    record["checks"] += 1
    # A rejection that asks for less than the last one is the work converging, not stuck: only the others count.
    weight = len(unmet) + len(major or issues)
    previous = record.get("rejectionWeight")
    if previous is None or weight >= previous:
        record["rejectionStalls"] = record.get("rejectionStalls", 0) + 1
    record["rejectionWeight"] = weight
    listed = lambda items: "\n".join(f"- {item}" for item in items) or "- none"  # noqa: E731
    feedback = REVIEW_BACK.format(summary=summary, unmet=listed(unmet), major=listed(major or issues), minor=listed(minor))
    record.update(phase="working", checkFaults=0, feedback=feedback[:6000])
    if record["rejectionStalls"] >= MAX_CHECKS or record["checks"] >= MAX_REJECTIONS:
        return _stop(service, thread_id, record, GoalStatus.BLOCKED,
                     f"An independent check rejected the work {record['checks']} times: {summary}")
    service.weave_goals.put(thread_id, record)
    return True


def _should_deepen(record, deeper):
    hours = record.get("hours") or 0
    return bool(deeper and hours and record.get("deepens", 0) < MAX_DEEPENS
                and _left(record) > DEEPEN_TIME_LEFT * hours * 3600
                and record["tokens"] < (1 - DEEPEN_BUDGET_LEFT) * record["tokenBudget"])


def _made_progress(service, thread_id, turn):
    """Whether a turn got any real work done. Calling a tool that keeps failing is not progress."""
    return any(event.turn_id == turn and event.kind is AgentEventKind.TOOL_COMPLETED and event.data.get("ok")
               and event.data.get("tool") not in PLAN_TOOLS
               for event in service.store.events(thread_id, limit=EVENTS_READ))


def _failure(service, thread_id, turn):
    """What the runtime said about a failed turn."""
    for event in reversed(service.store.turn_events(thread_id, turn) if turn else ()):
        if event.kind is AgentEventKind.TURN_FAILED:
            return event.data
    return {}


def _settle_turn(service, thread_id, record, session):
    """Account for the turn that just ended; returns True when the goal stopped because of it."""
    turn = session.current_turn_id
    if turn == record.get("handledTurn"):
        return False
    record["handledTurn"] = turn
    if session.status is AgentStatus.CANCELLED:
        return _stop(service, thread_id, record, GoalStatus.PAUSED, "Stopped by the person")
    if session.status is AgentStatus.FAILED:
        now = time.time()
        record["failures"] += 1
        record["failingSince"] = record.get("failingSince") or now
        transient = _transient(_failure(service, thread_id, turn))
        if (now - record["failingSince"] > RETRY_WINDOW) if transient else record["failures"] > MAX_FAILURES:
            return _stop(service, thread_id, record, GoalStatus.BLOCKED, str(session.error or "The turn failed"))
        record["retryAt"] = now + _delay(record["failures"])
        return False
    record.update(failures=0, failingSince=0)
    if turn == record.get("goalTurn"):
        record["stalls"] = 0 if _made_progress(service, thread_id, turn) else record["stalls"] + 1
        if record["stalls"] >= MAX_STALLS:
            return _stop(service, thread_id, record, GoalStatus.BLOCKED,
                         f"{MAX_STALLS} turns in a row made no progress: {str(session.final_text or '')[:600]}")
    return False


def _continue(service, thread_id, record, session):
    if record["turns"] >= MAX_TURNS:
        return _stop(service, thread_id, record, GoalStatus.BLOCKED, f"Reached {MAX_TURNS} turns")
    note, turn_id = record["feedback"], str(uuid.uuid4())
    context = _context(service, thread_id, record)
    record.update(turns=record["turns"] + 1, feedback="", deepen="", reports="", goalTurn=turn_id)
    service.weave_goals.put(thread_id, record)

    def run():
        service._prepare_turn(session)
        return service.runtime.continue_goal(thread_id, note=note, turn_id=turn_id, context=context)

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
    if goal_lines.live(record) and now - polled.get((thread_id, "lines"), 0) >= LINES_EVERY:
        polled[(thread_id, "lines")] = now
        if goal_lines.advance(service, thread_id, record):
            service.weave_goals.put(thread_id, record)
    if service._is_active(thread_id):
        if now - polled.get(thread_id, 0) < USAGE_EVERY:
            return False
    elif goal_lines.live(record) and now - polled.get(thread_id, 0) < CHECK_EVERY:
        return False
    elif record["phase"] == "checking" and now - polled.get(thread_id, 0) < CHECK_EVERY:
        return False
    elif now < record["retryAt"]:
        return False
    polled[thread_id] = now
    session = _charge(service, thread_id, record)
    if record["tokens"] >= record["tokenBudget"]:
        claimed = record["phase"] == "checking" or goal.status is GoalStatus.COMPLETE
        _stop(service, thread_id, record, GoalStatus.BUDGET_LIMITED, OUT_CHECKING if claimed else OUT_WORKING)
        if session.status in _BUSY or service._is_active(thread_id):
            service.runtime.cancel(thread_id)
        return True
    if record["phase"] == "working" and record["tokens"] >= WRAP_SHARE * record["tokenBudget"]:
        record["wrapUp"] = True  # the next tool result of the running turn says so, and every goal turn after
    elif record.get("wrapUp"):  # a larger budget, or the check: said again only if it comes back
        record.update(wrapUp=False, wrapTold=False)
    service.weave_goals.put(thread_id, record)
    if service._is_active(thread_id) or session.status in _BUSY:
        return False
    if goal.status is GoalStatus.COMPLETE:  # the agent's claim, not yet a fact
        service.runtime.set_goal_status(thread_id, GoalStatus.ACTIVE)
        if not (goal_lines.live(record) or record.get("reports")):
            _spawn_checker(service, thread_id, record, goal.objective)
            return True
        # Claimed with reports unread: nothing is checked until the lead has integrated them.
    if record["phase"] == "checking":
        return _collect(service, thread_id, record, goal.objective)
    if _settle_turn(service, thread_id, record, session):
        return True
    service.weave_goals.put(thread_id, record)
    if now < record["retryAt"] or goal_lines.live(record):  # the lead costs nothing while it waits for its lines
        return False
    return _continue(service, thread_id, record, session)


def annotate(service, session, result):
    """A tool result of a goal's conversation as the model sees it: the first one after the goal passes WRAP_SHARE
    carries the note. Goal turns run for hours, and a tool result is where a running turn can be told."""
    if getattr(session, "weave", False) is not True:
        return result  # only build mode has goals; ordinary turns pay no file read per tool call
    record = service.weave_goals.get(session.session_id)
    if not record or not record.get("wrapUp") or record.get("wrapTold") or record.get("phase") != "working":
        return result
    with service.project_runner.guard:
        record = service.weave_goals.get(session.session_id)
        if not record or record.get("wrapTold"):
            return result
        record["wrapTold"] = True
        service.weave_goals.put(session.session_id, record)
    note = WRAP_UP.format(share=record["tokens"] / max(1, record["tokenBudget"]))
    return type(result)(result.ok, result.content + "\n\nRuntime note: " + note, result.data)


def observe_failure(service, event):
    """Runtime listener: a Weave turn the provider failed before any goal existed is tried again later.

    The goal's own retries only begin once weave_goal has run; a first turn that met a provider out of
    capacity used to end there, and the person who sent it and went to bed found nothing in the morning.
    Only the facts are written here; the scheduler decides, with the session at rest.
    """
    if event.kind in {AgentEventKind.TURN_COMPLETED, AgentEventKind.TURN_CANCELLED}:
        if service.weave_retries.get(event.session_id):
            service.weave_retries.take(event.session_id)  # it went through, or the person stopped it
        return
    if event.kind is not AgentEventKind.TURN_FAILED or not _transient(event.data):
        return
    thread_id, now = event.session_id, time.time()
    if thread_id in service._project_session_ids or service.weave_goals.get(thread_id):
        return  # a project's own agents and a goal's turns have their own retries
    earlier = service.weave_retries.get(thread_id) or {}
    if earlier.get("retrying") != event.turn_id:
        earlier = {}  # the person's own new message starts its own wait
    since = earlier.get("since") or now
    if now - since > RETRY_WINDOW:
        service.weave_retries.take(thread_id)
        return
    attempts = int(earlier.get("attempts", 0)) + 1
    service.weave_retries.put(thread_id, {"turn": event.turn_id, "attempts": attempts, "since": since,
                                          "retryAt": now + _delay(attempts)})


def _retry_start(service, thread_id, record):
    """Run a failed Weave turn again once its wait is over; anything the person did since cancels it."""
    if service._is_active(thread_id):
        return False
    session = service.store.load(thread_id)
    if (not getattr(session, "weave", False) or session.status is not AgentStatus.FAILED
            or session.current_turn_id != record.get("turn") or service.weave_goals.get(thread_id)):
        service.weave_retries.take(thread_id)
        return False
    if time.time() < record.get("retryAt", 0):
        return False
    turn_id, last = str(uuid.uuid4()), session.messages[-1] if session.messages else None
    # Until the attempt reports back, never twice at once; its failure continues this wait.
    record.update(retryAt=time.time() + RETRY_MAX, retrying=turn_id)
    service.weave_retries.put(thread_id, record)
    unanswered = last is not None and last.role is MessageRole.USER and not last.name

    def run():
        service._prepare_turn(session)
        if unanswered:  # the same input again: the runtime treats it as a retry, not a second message
            return service.runtime.start_turn(thread_id, last.content, turn_id=turn_id)
        return service.runtime._start_turn_once(thread_id, RETRY_NOTE, source="goal", turn_id=turn_id)

    service._launch(thread_id, run)
    return True


def advance(service):
    """One scheduler tick: bring each long goal one move forward; returns whether anything moved."""
    polled = service.__dict__.setdefault("_goal_polled", {})
    moved = False
    retries = getattr(service, "weave_retries", None)
    for thread_id, record in (retries.all().items() if retries else ()):
        try:
            moved = _retry_start(service, thread_id, record) or moved
        except (OSError, ValueError, RuntimeError) as exc:
            service.__dict__.setdefault("_goal_errors", {})[thread_id] = f"{type(exc).__name__}: {exc}"
    for thread_id, record in service.weave_goals.all().items():
        try:
            moved = _advance(service, thread_id, record, polled) or moved
        except (OSError, ValueError, RuntimeError) as exc:
            # A goal must never take the scheduler down; a session being written is read again next tick.
            service.__dict__.setdefault("_goal_errors", {})[thread_id] = f"{type(exc).__name__}: {exc}"
    return moved


def control(service, params):
    """The hub's stop, explicit budget changes, resume and dismiss."""
    thread_id = service._required_text(params, "threadId")
    action, record = params.get("action"), service.weave_goals.get(thread_id)
    if action == "resume_with_budget":
        if "tokenBudget" not in params:
            raise ValueError("Explicit tokenBudget is required to resume with a budget")
        action = "resume"
    if record is None or service.runtime.get_goal(thread_id) is None:
        raise ValueError("This conversation has no long goal")
    if "tokenBudget" in params and action not in {"resume", "set_budget"}:
        raise ValueError("Budget changes require set_budget or resume")
    if action in {"resume", "set_budget"}:
        if record["phase"] == "done":
            raise ValueError("This goal is finished")
        if "expectedStartedAt" in params and params["expectedStartedAt"] != record["startedAt"]:
            raise ValueError("The goal changed; refresh before changing its budget")
        _charge(service, thread_id, record)
        if "tokenBudget" in params:
            budget = params["tokenBudget"]
            if type(budget) is not int or not 1 <= budget <= 1_000_000_000 or budget <= record["tokens"]:
                raise ValueError("Total token budget must be an integer above usage and at most 1000000000")
            expected = params.get("expectedTokenBudget", record["tokenBudget"])
            if expected != record["tokenBudget"] and budget != record["tokenBudget"]:
                raise ValueError("The budget changed; refresh before changing it again")
            record["tokenBudget"] = budget
        elif action == "set_budget":
            raise ValueError("tokenBudget is required")
        if action == "resume" and record["tokens"] >= record["tokenBudget"]:
            raise ValueError("Goal budget exhausted; explicitly set a new total token budget before resuming")
    if action == "pause":
        _stop(service, thread_id, record, GoalStatus.PAUSED, "Stopped by the person")
        if service._is_active(thread_id):
            service.runtime.cancel(thread_id)
    elif action == "resume":
        record.update(reason="", stalls=0, failures=0, failingSince=0, retryAt=0, handledTurn=service.store.load(thread_id).current_turn_id)
        service.weave_goals.put(thread_id, record)
        service.runtime.set_goal_status(thread_id, GoalStatus.ACTIVE)
    elif action == "set_budget":
        service.weave_goals.put(thread_id, record)
    elif action == "dismiss":
        goal_lines.stop_all(service, thread_id, record, "the person dismissed the goal")
        service.runtime.clear_goal(thread_id)
        service.weave_goals.take(thread_id)
    else:
        raise ValueError("action must be pause, resume, resume_with_budget, set_budget or dismiss")
    return {"goal": view(service, thread_id)}


def view(service, thread_id):
    record = service.weave_goals.get(thread_id)
    goal = service.runtime.get_goal(thread_id) if record else None
    if goal is None:
        return None
    claimed = goal.status is GoalStatus.COMPLETE and record["phase"] != "done"  # until the check says so, it is not done
    phase = "checking" if claimed else record["phase"]
    if phase == "working" and service.store.load(thread_id).status is AgentStatus.WAITING_APPROVAL:
        phase = "approval"  # nothing moves until the person answers, however long the night
    return {"objective": goal.objective[:300], "status": "active" if claimed else goal.status.value,
            "startedAt": record["startedAt"],
            "phase": phase,
            "tokens": record["tokens"], "tokenBudget": record["tokenBudget"], "turns": record["turns"],
            "reason": record["reason"], "summary": record["summary"], "checked": record["checked"],
            "retryAt": record.get("checkerRetryAt", 0) if phase == "checking" else record["retryAt"] if record.get("failures") and phase == "working" else 0,
            "hours": record.get("hours") or 0, "deepens": record.get("deepens", 0),
            "agents": _working_agents(service, thread_id, record), "lines": goal_lines.view(record),
            **goal_charter.view(record)}
