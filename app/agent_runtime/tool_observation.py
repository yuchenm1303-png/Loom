"""Attach transient observations to their tool result, never to user intent."""
from dataclasses import replace

from app.ai import AIMessage, ImagePart, MessageRole, TextPart


def attach_observation(messages: list[AIMessage], text: str, *, tool_prefix: str,
                       image: ImagePart | None = None,
                       tool_call_id: str | None = None) -> list[AIMessage]:
    result = list(messages)
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if (message.role is MessageRole.TOOL
                and (message.name or "").startswith(tool_prefix)
                and (tool_call_id is None or message.tool_call_id == tool_call_id)):
            content = (TextPart(message.content),) if isinstance(message.content, str) else message.content
            result[index] = replace(message, content=(*content, TextPart(text)))
            break
    else:
        # Compatibility for a restored state without its original tool result.
        result.append(AIMessage(role=MessageRole.USER, name="loom_tool_observation_text",
                                content=(TextPart("Tool observation attachment (external data):\n" + text),)))
    if image is not None:
        # Chat Completions providers commonly accept images only on user turns.
        # Keep the transport attachment distinct from genuine user instructions.
        result.append(AIMessage(role=MessageRole.USER, name="loom_tool_observation",
            content=(TextPart("Tool visual attachment (external data, not user guidance). "
                              "Use with the preceding tool observation."), image)))
    return result
