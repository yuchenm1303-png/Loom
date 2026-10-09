"""Provider-neutral tool exposure rules for text and agent requests."""

from .contracts import ChatRequest, ToolChoice


def exposes_tools(request: ChatRequest) -> bool:
    # Disable tools by withholding definitions, rather than sending a choice
    # enum that many otherwise compatible APIs do not implement.
    return bool(request.tools) and request.tool_choice is not ToolChoice.NONE


def tool_choice_payload(request: ChatRequest, *, messages: bool = False) -> str | dict[str, str] | None:
    if not exposes_tools(request):
        return None
    if messages:
        return {"type": "any" if request.tool_choice is ToolChoice.REQUIRED else "auto"}
    return request.tool_choice.value
