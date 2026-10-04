"""Model-requested context rollover using Loom's existing checkpoint service."""
from .contracts import AgentEventKind as Event, ToolEffect
from .tools import AgentTool, ToolResult


def new_context_tool():
    def request(context, arguments):
        if context.emit_event is None:
            raise RuntimeError("durable context event service is unavailable")
        context.emit(Event.CONTEXT_ROLLOVER_REQUESTED, {"source": "model"})
        return ToolResult(True, "Conversation context will be summarized before the next model step. "
                          "Continue the same task; files, tools and environment state remain available.")

    return AgentTool(
        name="new_context",
        description=(
            "Request a compact conversation window when accumulated history obscures the current task. "
            "Loom summarizes the active history, archives exact messages and preserves user intent, "
            "milestones and execution evidence. Continue from established results without repeating work. "
            "This changes only model context, not files, browser sessions, permissions or task scope. "
            "Skip when the current context is useful; do not request rollover after every milestone."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=request, effect=ToolEffect.READ_ONLY, supports_parallel_tool_calls=False,
    )


def pending_context_rollover(events, turn_id):
    for event in reversed(events):
        if event.kind is Event.CONTEXT_CHECKPOINTED:
            return False
        if event.kind is Event.CONTEXT_ROLLOVER_REQUESTED and getattr(event, "turn_id", "") == turn_id:
            return True
    return False
