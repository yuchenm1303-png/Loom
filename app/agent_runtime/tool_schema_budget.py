from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Iterable

from .json_schema_semantics import validating_schema
from .tools import AgentTool, ToolRouter


_CORE_TOOL_NAMES = frozenset(
    {
        "tool_search",
        "exec",
        "apply_patch",
        "read_workspace_text",
        "list_workspace_files",
        "get_turn_diff",
    }
)

# A capability is unusable without its own verbs. Ranking shedding purely by
# schema size dropped browser_click and browser_type, leaving a browser the model
# could open and inspect but never interact with, and dropped spawn_agent, the
# only entry point to delegation. These stay resident so shedding falls on tools
# whose absence costs a lookup rather than a capability.
_CAPABILITY_ACTION_NAMES = frozenset(
    {
        "browser_open",
        "browser_state",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "computer_run_task",
        "spawn_agent",
        "wait_agent",
    }
)


@dataclass(frozen=True, slots=True)
class ToolSchemaPlan:
    router: ToolRouter
    mode: str
    original_schema_tokens: int
    planned_schema_tokens: int
    omitted_names: tuple[str, ...] = ()
    pinned_names: tuple[str, ...] = ()

    @property
    def reduced(self) -> bool:
        return self.mode != "full" or bool(self.omitted_names)

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "original_schema_tokens": self.original_schema_tokens,
            "planned_schema_tokens": self.planned_schema_tokens,
            "omitted_count": len(self.omitted_names),
            "omitted_names": list(self.omitted_names),
            "pinned_names": list(self.pinned_names),
        }


def schema_token_budget(input_budget_tokens: int) -> int:
    """Bound fixed tool-schema overhead before it crowds out task context.

    Twenty percent is deliberately a soft budget, not a hard provider limit. It
    leaves most of the window for instructions, conversation, observations and
    output headroom while still allowing a substantial direct tool surface.
    """

    budget = max(1, int(input_budget_tokens))
    return max(1_200, min(12_000, budget // 5))


def estimate_tool_schema_tokens(tools: Iterable[AgentTool]) -> int:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        for tool in tools
    ]
    if not payload:
        return 0
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return math.ceil(len(encoded) / 3) + 12 * len(payload)


def _short_description(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    candidate = normalized[: max(1, limit - 1)].rstrip()
    if " " in candidate:
        candidate = candidate.rsplit(" ", 1)[0]
    return candidate.rstrip(" ,;:") + "…"


def _project_tool(tool: AgentTool, *, mode: str) -> AgentTool:
    if mode == "full":
        return tool
    if mode == "compact":
        return replace(
            tool,
            description=_short_description(tool.description, limit=240),
            input_schema=validating_schema(tool.input_schema),
        )
    if mode == "structural":
        return replace(
            tool,
            description=_short_description(tool.description, limit=112),
            input_schema=validating_schema(tool.input_schema),
        )
    raise ValueError(f"unknown tool schema projection mode: {mode}")


def _project_router(
    router: ToolRouter,
    *,
    mode: str,
    names: Iterable[str] | None = None,
) -> ToolRouter:
    wanted = None if names is None else {str(name) for name in names}
    tools = tuple(
        _project_tool(tool, mode=mode)
        for tool in router.all()
        if wanted is None or tool.name in wanted
    )
    return ToolRouter(tools)


def _priority(name: str, pinned: set[str]) -> int:
    if name in pinned:
        return 0
    if name == "tool_search":
        return 1
    if name in _CORE_TOOL_NAMES:
        return 2
    if name in _CAPABILITY_ACTION_NAMES:
        return 3
    if name.startswith("exec_"):
        return 4
    if name.startswith(("read_", "list_", "get_", "search_")):
        return 5
    return 6


def plan_tool_schema_pressure(
    router: ToolRouter,
    *,
    max_schema_tokens: int,
    pinned_names: Iterable[str] = (),
    allow_shedding: bool = True,
) -> ToolSchemaPlan:
    """Create a request-scoped tool router that fits a soft schema budget.

    The planner never mutates the registry. First it removes non-validating JSON
    Schema annotations while preserving argument structure. Only when that is
    insufficient, and ``tool_search`` is available, does it temporarily omit
    lower-priority direct tools. Those omitted names can be made searchable by
    ToolSearchRuntime and pinned back into the next request after discovery.
    """

    soft_limit = max(1, int(max_schema_tokens))
    original_tools = router.all()
    original_tokens = estimate_tool_schema_tokens(original_tools)
    pinned = {str(name) for name in pinned_names if str(name)}
    present = {tool.name for tool in original_tools}
    pinned.intersection_update(present)

    if original_tokens <= soft_limit:
        return ToolSchemaPlan(
            router=router,
            mode="full",
            original_schema_tokens=original_tokens,
            planned_schema_tokens=original_tokens,
            pinned_names=tuple(sorted(pinned)),
        )

    compact_router = _project_router(router, mode="compact")
    compact_tokens = estimate_tool_schema_tokens(compact_router.all())
    if compact_tokens <= soft_limit:
        return ToolSchemaPlan(
            router=compact_router,
            mode="compact",
            original_schema_tokens=original_tokens,
            planned_schema_tokens=compact_tokens,
            pinned_names=tuple(sorted(pinned)),
        )

    # Shedding without a discovery path would silently remove capabilities. In
    # that case keep every compacted tool and let ContextBudgetExceeded report a
    # precise hard-limit failure instead of making the agent deceptively weaker.
    if not allow_shedding or "tool_search" not in present:
        return ToolSchemaPlan(
            router=compact_router,
            mode="compact",
            original_schema_tokens=original_tokens,
            planned_schema_tokens=compact_tokens,
            pinned_names=tuple(sorted(pinned)),
        )

    projected = {
        tool.name: _project_tool(tool, mode="structural")
        for tool in original_tools
    }
    per_tool_tokens = {
        name: estimate_tool_schema_tokens((tool,))
        for name, tool in projected.items()
    }

    selected: set[str] = set()
    mandatory = set(pinned)
    mandatory.add("tool_search")
    for name in sorted(mandatory, key=lambda item: (_priority(item, pinned), item)):
        if name in projected:
            selected.add(name)

    # Within one capability tier, prefer smaller schemas so the same fixed budget
    # retains more callable surface area. Names are only the deterministic final
    # tie-breaker, never the capacity policy.
    candidates = sorted(
        (name for name in projected if name not in selected),
        key=lambda name: (_priority(name, pinned), per_tool_tokens[name], name),
    )
    for name in candidates:
        candidate_tokens = estimate_tool_schema_tokens(
            projected[item] for item in (*sorted(selected), name)
        )
        if candidate_tokens <= soft_limit:
            selected.add(name)

    # If the soft budget is smaller than mandatory discovery/pinned tools, keep
    # them anyway. The hard context budget remains authoritative and will emit a
    # precise diagnostic if even this minimal viable tool surface cannot fit.
    planned_router = ToolRouter(tuple(projected[name] for name in sorted(selected)))
    planned_tokens = estimate_tool_schema_tokens(planned_router.all())
    omitted = tuple(sorted(present - selected))
    return ToolSchemaPlan(
        router=planned_router,
        mode="structural",
        original_schema_tokens=original_tokens,
        planned_schema_tokens=planned_tokens,
        omitted_names=omitted,
        pinned_names=tuple(sorted(pinned)),
    )


__all__ = [
    "ToolSchemaPlan",
    "estimate_tool_schema_tokens",
    "plan_tool_schema_pressure",
    "schema_token_budget",
]
