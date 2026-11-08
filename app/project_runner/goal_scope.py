"""In build mode the Host, not the model's reading of keywords, makes sure each request is sized.

Before the first call that changes anything in a person's request, with no long goal and no project running,
the agent must choose: weave_goal (work worth a goal: research, experiments, several steps that feed each other,
anything that deserves an independent check) or weave_one_turn (a quick task it can finish well in this turn).
A one-turn choice that keeps growing is asked again once. Reading is never held, and the gate gives way after
MAX_HOLDS refusals so it can never cost the turn.
"""
from __future__ import annotations

from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.durable_state import GoalStatus
from app.agent_runtime.tools import ToolResult

from . import goal_charter

FAILURE_KIND = "goal_scope_required"
MAX_HOLDS = 2  # refusals per request before the gate gives way
GROW_AFTER = 30  # changing calls in a one-turn request before it is asked again
MIN_REASON = 15
LOOKUPS = frozenset({"web_search", "web_search_status"})  # answering a question changes nothing here

HELD = ("Not executed: in build mode, decide this request's size before changing anything (reading is fine). "
        "If it is more than a quick task (research, experiments, a study, several steps that feed each other, "
        "anything worth an independent check, or work that should go on after this turn), call weave_goal now; no "
        "time needs to be named, and then write its charter. Only if you can finish it well within this one turn, "
        "call weave_one_turn with why. When unsure, it is a goal.")
GREW = ("Not executed: this request was sized as one turn, but it has grown past a quick task ({count} changing "
        "actions so far). If it needs more, call weave_goal now; what you did carries over and the goal gets it "
        "checked. If it really ends in this turn, call weave_one_turn again with why.")
CHOSEN = "Sized as one turn. If it grows past that, call weave_goal; work done so far carries over."


def _scopes(service):
    return service.__dict__.setdefault("weave_scopes", {})


def choose_one_turn(service, session_id, turn_id, reason):
    reason = str(reason or "").strip()
    if len(reason) < MIN_REASON:
        raise ValueError(f"reason must say in at least {MIN_REASON} characters why this ends in this turn")
    with service.project_runner.guard:
        scope = _scopes(service).get(session_id)
        if not scope or scope.get("turn") != turn_id:
            scope = {"turn": turn_id, "holds": 0, "calls": 0}
        scope.update(choice="turn", reason=reason[:400], asked=scope.get("calls", 0))
        _scopes(service)[session_id] = scope
    return CHOSEN


def _goal_running(service, session_id):
    if not service.weave_goals.get(session_id):
        return False
    current = service.runtime.get_goal(session_id)
    return current is not None and current.status is GoalStatus.ACTIVE


def _project_running(service, session_id):
    from . import weave
    return weave.current_project(service, session_id)[1] is not None


def hold(service, session, step, calls):
    """Before a batch runs in a person's build-mode request that has not been sized: hold what changes things."""
    if getattr(session, "weave", False) is not True or session.session_id in getattr(service, "_project_session_ids", ()):
        return None
    turn = getattr(session, "current_turn_id", None)
    if not turn or _goal_running(service, session.session_id):
        return None

    def free(call):
        if call.name in goal_charter._FREE or call.name in LOOKUPS or call.name.startswith(goal_charter.WEAVE_PREFIX):
            return True
        tool = step.tool_router.get(call.name) if step is not None else None
        return tool is not None and tool.effect is ToolEffect.READ_ONLY and call.name != "spawn_agent"

    waiting = tuple(call for call in calls if not free(call))
    if not waiting:
        return None
    with service.project_runner.guard:
        scope = _scopes(service).get(session.session_id)
        if not scope or scope.get("turn") != turn:
            scope = {"turn": turn, "holds": 0, "calls": 0}
            _scopes(service)[session.session_id] = scope
        if scope.get("choice") == "gave_way":
            return None
        if scope.get("choice") == "turn":
            if scope.get("regrown") or scope["calls"] - scope.get("asked", 0) < GROW_AFTER:
                scope["calls"] += len(waiting)
                return None
            scope.update(regrown=True, choice=None, holds=0)
            text = GREW.format(count=scope["calls"])
        else:
            if scope.get("project") is None:  # a project running here is sized already; looked up once per request
                scope["project"] = _project_running(service, session.session_id)
            if scope["project"]:
                return None
            text = GREW.format(count=scope["calls"]) if scope.get("regrown") else HELD
        scope["holds"] += 1
        if scope["holds"] > MAX_HOLDS:  # it asks for a decision, it guards nothing: never at the cost of the turn
            scope["choice"] = "gave_way"
            return None
    refusal = ToolResult(False, text, {"failure_kind": FAILURE_KIND, "execution_status": "not_executed",
                                       "retry_policy": "decide_goal_or_one_turn"})
    return tuple((call, refusal) for call in waiting)
