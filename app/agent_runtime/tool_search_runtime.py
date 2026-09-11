from __future__ import annotations

import re
import threading
from dataclasses import replace
from typing import Any

from .context_limits import resolve_context_limits
from .contracts import AgentSession, AgentStatus, ToolEffect
from .mcp_configured_runtime import ConfiguredMCPRuntime
from .step import StepContext
from .tool_schema_budget import plan_tool_schema_pressure, schema_token_budget
from .tools import AgentTool, ToolContext, ToolExposure, ToolRegistry, ToolResult


_QUERY_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)


def _query_tokens(value: str) -> tuple[str, ...]:
    return tuple(_QUERY_TOKEN_RE.findall(str(value or "").casefold()))


def _tool_match_score(tool: AgentTool, query: str) -> int:
    raw_query = str(query or "").strip()
    if not raw_query:
        return 0
    query_folded = raw_query.casefold()
    query_tokens = _query_tokens(raw_query)
    name_folded = tool.name.casefold()
    description_folded = tool.description.casefold()
    name_tokens = set(_query_tokens(tool.name))
    description_tokens = set(_query_tokens(tool.description))
    score = 0

    if query_folded == name_folded:
        score += 10_000
    elif name_folded.startswith(query_folded):
        score += 7_000
    elif query_folded in name_folded:
        score += 5_000
    elif query_folded in description_folded:
        score += 1_500

    for token in query_tokens:
        if token in name_tokens:
            score += 700
        elif any(part.startswith(token) or token.startswith(part) for part in name_tokens):
            score += 350
        if token in description_tokens:
            score += 120
    return score


class ToolSearchRuntime(ConfiguredMCPRuntime):
    """Runtime layer that discovers deferred and schema-shed tools on demand.

    Deferred activation is intentionally scoped to one active turn. Context
    Runtime v2 can additionally shed direct tool schemas when their fixed prompt
    cost crowds out useful task context. Shedding is request-scoped only: the
    full registry remains intact, ``tool_search`` can still discover shed tools,
    and a discovered tool is pinned back into the next model step.
    """

    def __init__(
        self,
        *args: Any,
        defer_mcp_tools: bool = True,
        **kwargs: Any,
    ) -> None:
        self._tool_search_guard = threading.RLock()
        self._turn_activations: dict[tuple[str, str], set[str]] = {}
        self._turn_schema_shed: dict[tuple[str, str], set[str]] = {}
        self._turn_schema_plan: dict[tuple[str, str, str], dict[str, object]] = {}
        self.defer_mcp_tools = bool(defer_mcp_tools)
        super().__init__(*args, **kwargs)

        # Codex-style default: once tool search exists, direct MCP tools move out
        # of the initial model context. Explicit hidden/code-mode classifications
        # remain untouched. Embedders can set defer_mcp_tools=False when they
        # intentionally want the lower-level MCP direct-exposure behavior.
        if self.defer_mcp_tools:
            rebuilt: list[AgentTool] = []
            for existing in self.tools.all():
                if existing.name.startswith("mcp.") and existing.exposure is ToolExposure.DIRECT:
                    existing = replace(existing, exposure=ToolExposure.DEFERRED)
                rebuilt.append(existing)
            self.tools = ToolRegistry(tuple(rebuilt))

        tool = self._tool_search_tool()
        if self.tools.get(tool.name) is not None:
            raise ValueError(f"tool search conflicts with existing tool: {tool.name}")
        self.tools.register(tool)

    def _tool_search_tool(self) -> AgentTool:
        return AgentTool(
            name="tool_search",
            description=(
                "Search tools that are registered but deferred from the model context. "
                "This also discovers direct tools temporarily omitted because tool schemas are consuming too much "
                "context. Matching tools become available on the next model step for this turn only. "
                "Use a concise capability query such as 'github create issue', 'wait process', or 'calendar events'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Capability, service, or action to find.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum matches to activate, from 1 to 20. Defaults to 5.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=self._search_tools,
            effect=ToolEffect.READ_ONLY,
            exposure=ToolExposure.DIRECT,
        )

    def _schema_shed_names(self, session_id: str, turn_id: str) -> tuple[str, ...]:
        key = (str(session_id), str(turn_id))
        with self._tool_search_guard:
            return tuple(sorted(self._turn_schema_shed.get(key, set())))

    def _set_context_shed_tools(
        self,
        session_id: str,
        turn_id: str,
        names,
    ) -> None:
        key = (str(session_id), str(turn_id))
        resolved = {
            str(name)
            for name in names
            if str(name) and self.tools.get(str(name)) is not None
        }
        with self._tool_search_guard:
            if resolved:
                self._turn_schema_shed[key] = resolved
            else:
                self._turn_schema_shed.pop(key, None)

    def _search_tools(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("tool search query must not be empty")
        limit = max(1, min(20, int(arguments.get("limit", 5))))

        candidates: dict[str, AgentTool] = {
            tool.name: tool
            for tool in self.tools.deferred()
        }
        for name in self._schema_shed_names(context.session_id, context.turn_id):
            tool = self.tools.get(name)
            if tool is not None:
                candidates[tool.name] = tool

        scored: list[tuple[int, str, AgentTool]] = []
        for tool in candidates.values():
            score = _tool_match_score(tool, query)
            if score > 0:
                scored.append((score, tool.name, tool))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matches = tuple(item[2] for item in scored[:limit])
        names = tuple(tool.name for tool in matches)
        if names:
            key = (context.session_id, context.turn_id)
            with self._tool_search_guard:
                self._turn_activations.setdefault(key, set()).update(names)

        shed = set(self._schema_shed_names(context.session_id, context.turn_id))
        records = [
            {
                "name": tool.name,
                "description": tool.description[:800],
                "effect": tool.effect.value,
                "source": "schema_pressure" if tool.name in shed else "deferred",
            }
            for tool in matches
        ]
        if records:
            content = "Tools activated for the next model step: " + ", ".join(names)
        else:
            content = f"No deferred or context-shed tools matched: {query}"
        return ToolResult(
            ok=True,
            content=content,
            data={
                "query": query,
                "count": len(records),
                "activated": list(names),
                "tools": records,
            },
        )

    def _activation_names(self, session: AgentSession) -> tuple[str, ...]:
        key = (session.session_id, session.current_turn_id)
        with self._tool_search_guard:
            return tuple(sorted(self._turn_activations.get(key, set())))

    def _activate_deferred_name(self, session_id: str, turn_id: str, tool_name: str) -> None:
        # The name is historical. Runtime v2 also uses this path to pin a direct
        # tool that was temporarily shed from a request because of schema pressure.
        tool = self.tools.get(tool_name)
        if tool is None or tool.exposure in {ToolExposure.HIDDEN, ToolExposure.CODE_MODE_ONLY}:
            return
        key = (str(session_id), str(turn_id))
        with self._tool_search_guard:
            self._turn_activations.setdefault(key, set()).add(tool.name)

    def _clear_session_activations(self, session_id: str) -> None:
        wanted = str(session_id)
        with self._tool_search_guard:
            stale = [key for key in self._turn_activations if key[0] == wanted]
            for key in stale:
                self._turn_activations.pop(key, None)
            stale_shed = [key for key in self._turn_schema_shed if key[0] == wanted]
            for key in stale_shed:
                self._turn_schema_shed.pop(key, None)
            stale_plans = [key for key in self._turn_schema_plan if key[0] == wanted]
            for key in stale_plans:
                self._turn_schema_plan.pop(key, None)

    def tool_schema_plan(
        self,
        session_id: str,
        turn_id: str,
        step_id: str,
    ) -> dict[str, object] | None:
        key = (str(session_id), str(turn_id), str(step_id))
        with self._tool_search_guard:
            value = self._turn_schema_plan.get(key)
            return dict(value) if value is not None else None

    def _build_step_context(
        self,
        session: AgentSession,
        *,
        next_model_step: bool,
        step_id: str | None = None,
    ) -> StepContext:
        step = super()._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        activations = self._activation_names(session)
        base_router = self.tools.router(activated_names=activations)
        limits = resolve_context_limits(self, session)
        plan = plan_tool_schema_pressure(
            base_router,
            max_schema_tokens=schema_token_budget(limits.input_budget_tokens),
            pinned_names=activations,
            allow_shedding=True,
        )
        self._set_context_shed_tools(
            session.session_id,
            session.current_turn_id,
            plan.omitted_names,
        )
        with self._tool_search_guard:
            self._turn_schema_plan[(session.session_id, session.current_turn_id, step.step_id)] = (
                plan.as_safe_dict()
            )
        router = plan.router
        return replace(
            step,
            tool_router=router,
            world_state=replace(
                step.world_state,
                tool_names=tuple(tool.name for tool in router.all()),
            ),
        )

    def _prepare_model_request(self, session, step, token):
        messages, extra = super()._prepare_model_request(session, step, token)
        plan = self.tool_schema_plan(
            session.session_id,
            session.current_turn_id,
            step.step_id,
        )
        if plan is None:
            return messages, extra
        metadata = dict(extra)
        metadata["tool_schema_plan"] = plan
        return messages, metadata

    def start_turn(
        self,
        session_id: str,
        user_text,
        *,
        turn_id: str | None = None,
    ):
        self._clear_session_activations(session_id)
        result = super().start_turn(session_id, user_text, turn_id=turn_id)
        if result.status is not AgentStatus.WAITING_APPROVAL:
            self._clear_session_activations(session_id)
        return result

    def resume_approval(self, session_id: str, call_id: str, *, approved: bool):
        # Reconstruct the exact pending tool before the parent runtime rebuilds
        # its immutable StepContext. This makes approval resume safe even after a
        # host restart erased turn-scoped activation/schema-pressure memory.
        session = self.store.load(session_id)
        pending = session.pending_approval
        if pending is not None and pending.call_id == str(call_id or "").strip():
            for call in session.pending_tool_calls:
                self._activate_deferred_name(session.session_id, session.current_turn_id, call.name)
        result = super().resume_approval(session_id, call_id, approved=approved)
        if result.status is not AgentStatus.WAITING_APPROVAL:
            self._clear_session_activations(session_id)
        return result

    def cancel(self, session_id: str):
        result = super().cancel(session_id)
        self._clear_session_activations(session_id)
        return result

    def recover_interrupted(self, session_id: str):
        self._clear_session_activations(session_id)
        return super().recover_interrupted(session_id)

    def close(self) -> None:
        with self._tool_search_guard:
            self._turn_activations.clear()
            self._turn_schema_shed.clear()
            self._turn_schema_plan.clear()
        super().close()


__all__ = ["ToolSearchRuntime"]
