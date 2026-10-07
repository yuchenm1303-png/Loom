from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Iterable

from .json_schema_semantics import validating_schema
from .tools import AgentTool, ToolRouter


_MIN_SCHEMA_TOKENS = 1_200
_MAX_SCHEMA_TOKENS = 12_000


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


def schema_token_budget(
    input_budget_tokens: int,
    *,
    conversation_tokens: int = 0,
) -> int:
    """Soft target for compacting annotations; never removes capabilities."""

    budget = max(1, int(input_budget_tokens))
    ceiling = max(_MIN_SCHEMA_TOKENS, min(_MAX_SCHEMA_TOKENS, budget // 5))
    conversation = max(0, int(conversation_tokens))
    if conversation <= 0:
        return ceiling
    return max(_MIN_SCHEMA_TOKENS, min(ceiling, budget - conversation))


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
    raise ValueError(f"unknown tool schema projection mode: {mode}")


def _project_router(
    router: ToolRouter,
    *,
    mode: str,
) -> ToolRouter:
    tools = tuple(
        _project_tool(tool, mode=mode)
        for tool in router.all()
    )
    return ToolRouter(tools)


def plan_tool_schema_pressure(
    router: ToolRouter,
    *,
    max_schema_tokens: int,
    pinned_names: Iterable[str] = (),
) -> ToolSchemaPlan:
    """Reduce prompt overhead without changing execution capabilities.

    The schema budget is a soft preference. If every schema cannot fit that
    preference, retain the callable set and let the unified context budget
    compact history with the full schema cost included. Exposure belongs to the
    tool registry and explicit deferred activation, never to token pressure.
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
    return ToolSchemaPlan(
        router=compact_router,
        mode="compact",
        original_schema_tokens=original_tokens,
        planned_schema_tokens=compact_tokens,
        pinned_names=tuple(sorted(pinned)),
    )


__all__ = [
    "ToolSchemaPlan",
    "estimate_tool_schema_tokens",
    "plan_tool_schema_pressure",
    "schema_token_budget",
]
