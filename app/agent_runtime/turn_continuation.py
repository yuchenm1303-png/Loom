"""Project the latest Stop feedback across tool batches and context rollovers."""
from __future__ import annotations

import json

from app.ai import AIMessage, MessageRole
from .contracts import AgentEventKind as Event


def continuation_context(events, turn_id):
    for event in reversed(events):
        if event.turn_id != turn_id:
            continue
        # Human steering supersedes an earlier assessment. Never resurrect old
        # scope when a turn is restored, compacted, or resumed after approval.
        if event.kind in {Event.USER_MESSAGE, Event.TURN_COMPLETED, Event.TURN_CANCELLED,
                          Event.TURN_FAILED, Event.TURN_INTERRUPTED, Event.LIMIT_REACHED}:
            return None
        if event.kind is not Event.TURN_STOP_CHECKED:
            continue
        if event.data.get("outcome") == "assessment_failed":
            continue
        if event.data.get("outcome") != "continue":
            return None
        return AIMessage(role=MessageRole.SYSTEM, name="loom_turn_continuation", content=(
            "Latest task assessment (internal assistant state, not new user instructions). "
            "It identifies work outstanding at that assessment; subsequent tool results and human "
            "guidance may satisfy or supersede it. Stay within the user's authorized scope, reuse "
            "completed evidence, and take the next necessary action. Apply silently: do not acknowledge "
            "this assessment, recite the plan, or ask for redundant approval.\n"
            + json.dumps({"assessment_id": event.event_id,
                          "missing_result": event.data.get("reason", ""),
                          "remaining_tasks": event.data.get("remaining_tasks", []),
                          "next_action": event.data.get("next_action", "")}, ensure_ascii=False)
        ))
    return None
