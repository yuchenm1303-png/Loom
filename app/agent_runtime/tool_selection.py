"""User-selected tool surface. None keeps runtime automatic discovery; [] is chat only."""
from dataclasses import replace

from .tools import ToolRouter


def parse_tool_selection(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 4096:
        raise ValueError("toolSelection must be null or an array of tool names")
    if any(not isinstance(name, str) or not name.strip() or len(name) > 256 for name in value):
        raise ValueError("toolSelection contains an invalid tool name")
    return sorted(set(name.strip() for name in value))


def tool_is_selected(session, name):
    selected = getattr(session, "tool_selection", None)
    return selected is None or name in selected


def selected_step(session, step):
    if getattr(session, "tool_selection", None) is None:
        return step
    visible = tuple(tool for tool in step.tool_router.all() if tool_is_selected(session, tool.name))
    signatures = "\n".join(f"- {tool.name}" for tool in visible if tool.name != "code_mode")
    router = ToolRouter(tuple(
        replace(tool, description=tool.description.split("Callable signatures", 1)[0]
                + "Only user-selected nested tools are available:\n" + (signatures or "No nested tools selected."))
        if tool.name == "code_mode" else tool for tool in visible))
    return replace(step, tool_router=router, world_state=replace(
        step.world_state, tool_names=tuple(tool.name for tool in router.all())))
