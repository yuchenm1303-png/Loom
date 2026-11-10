"""Build mode's task list: a turn that uses tools shows its list before it works.

The list is the person's view of the turn, so the Host holds the work until it exists; a prompt alone
is advice a model may skip. A turn that only answers uses no tool and never meets this. The rule shows
progress and guards nothing, so it gives way after MAX_HOLDS refused batches: a model that will not
write a list loses the list, not the person's turn.
"""
from .contracts import AgentEventKind
from .plan_state import active_plan, from_earlier_request, unfinished
from .tool_selection import WEAVE_PREFIX
from .tools import ToolResult

FAILURE_KIND = "turn_plan_required"
MAX_HOLDS = 2
PLAN_TOOLS = frozenset({"update_plan", "manage_plan"})
# Not work to list: writing the list, finding a tool, and the mode's own project controls.
_FREE = PLAN_TOOLS | {"tool_search", "get_thread_state", "mark_thread_goal"}


def _refusal(previous, earlier=False):
    text = ("Not executed: in Build mode a turn that uses tools shows the person its task list first. "
            "Reissue these calls with update_plan first in the same response: at least two short steps, "
            "the one you are starting in_progress, the rest pending.")
    if previous and earlier:
        # A real model handed in the earlier request's list unchanged here and went on doing its work.
        text += (f" The active list was written for an earlier request (\"{previous.get('request') or 'not recorded'}\"), "
                 "not for the person's new message. Write this request's list with replace=true "
                 f"(plan_id={previous.get('plan_id')}, expected_revision={previous.get('revision', 0)}, an explanation); "
                 "continue the old one only if the new message carries it on, and then say so in the explanation.")
    elif previous:
        text += (f" An earlier list is still active (plan_id={previous.get('plan_id')}, "
                 f"expected_revision={previous.get('revision', 0)}): update it if this turn continues that work, "
                 "otherwise pass replace=true with those two values and an explanation.")
    return ToolResult(False, text, {"failure_kind": FAILURE_KIND, "execution_status": "not_executed",
                                    "retry_policy": "reissue_with_update_plan"})


def _carry_into_goal_turn(runtime, session, events):
    """A goal's continuation turn works the list the goal already has: the Host shows it for this turn.

    Every goal turn lost its first model step (a full re-send of a history of hundreds of thousands of tokens) to
    this refusal, and a prompt saying "start with update_plan" is advice a model may skip. The list is stated again
    unchanged, with the same revision so a plan_id/expected_revision the model holds stays valid; it is only
    carried while steps remain, so a finished list never stands in for the next stretch of work."""
    plan = active_plan(events)
    if not plan or not unfinished(plan):
        return False
    started = runtime._lifecycle_events(session.session_id, turn_id=session.current_turn_id,
                                        kinds=(AgentEventKind.TURN_STARTED,))
    if not any(event.data.get("source") == "goal" for event in started):
        return False
    carried = dict(plan)
    carried.update(updated_turn_id=session.current_turn_id, carried=True)
    runtime._emit_event(session, AgentEventKind.PLAN_UPDATED, data=carried)
    return True


def held_calls(runtime, session, step, calls):
    """The refusal for each call of a batch that must wait for the list, or None when all may run.

    What needs no list is not refused with what does."""
    if getattr(session, "weave", False) is not True or step.tool_router.get("update_plan") is None:
        return None
    waiting = tuple(call for call in calls if not (call.name in _FREE or call.name.startswith(WEAVE_PREFIX)))
    if not waiting:
        return None
    if any(call.name in PLAN_TOOLS for call in calls):
        return None
    events = runtime._lifecycle_events(session.session_id, kinds=(AgentEventKind.PLAN_UPDATED,))
    if any(event.turn_id == session.current_turn_id for event in events):
        return None
    if _carry_into_goal_turn(runtime, session, events):
        return None
    turn, holds = runtime._turn_plan_holds.get(session.session_id, (None, 0))
    holds = holds if turn == session.current_turn_id else 0
    if holds >= MAX_HOLDS:
        return None
    runtime._turn_plan_holds[session.session_id] = (session.current_turn_id, holds + 1)
    previous = active_plan(events)
    started = runtime._lifecycle_events(session.session_id, turn_id=session.current_turn_id,
                                        kinds=(AgentEventKind.TURN_STARTED,))
    refusal = _refusal(previous, from_earlier_request(previous, started, session.current_turn_id))
    return tuple((call, refusal) for call in waiting)
