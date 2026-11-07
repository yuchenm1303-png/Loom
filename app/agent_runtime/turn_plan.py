"""Build mode's task list: a turn that uses tools shows its list before it works.

The list is the person's view of the turn, so the Host holds the work until it exists; a prompt alone
is advice a model may skip. A turn that only answers uses no tool and never meets this. The rule shows
progress and guards nothing, so it gives way after MAX_HOLDS refused batches: a model that will not
write a list loses the list, not the person's turn.
"""
from .contracts import AgentEventKind
from .plan_state import active_plan
from .tool_selection import WEAVE_PREFIX
from .tools import ToolResult

FAILURE_KIND = "turn_plan_required"
MAX_HOLDS = 2
PLAN_TOOLS = frozenset({"update_plan", "manage_plan"})
# Not work to list: writing the list, finding a tool, and the mode's own project controls.
_FREE = PLAN_TOOLS | {"tool_search", "get_thread_state", "mark_thread_goal"}


def _refusal(previous):
    text = ("Not executed: in Build mode a turn that uses tools shows the person its task list first. "
            "Reissue these calls with update_plan first in the same response: at least two short steps, "
            "the one you are starting in_progress, the rest pending.")
    if previous:
        text += (f" An earlier list is still active (plan_id={previous.get('plan_id')}, "
                 f"expected_revision={previous.get('revision', 0)}): update it if this turn continues that work, "
                 "otherwise pass replace=true with those two values and an explanation.")
    return ToolResult(False, text, {"failure_kind": FAILURE_KIND, "execution_status": "not_executed",
                                    "retry_policy": "reissue_with_update_plan"})


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
    turn, holds = runtime._turn_plan_holds.get(session.session_id, (None, 0))
    holds = holds if turn == session.current_turn_id else 0
    if holds >= MAX_HOLDS:
        return None
    runtime._turn_plan_holds[session.session_id] = (session.current_turn_id, holds + 1)
    refusal = _refusal(active_plan(events))
    return tuple((call, refusal) for call in waiting)
