from __future__ import annotations

import math
import re
import threading
from dataclasses import replace
from typing import Any

from .context_budget import estimate_tokens
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
    """Discover explicitly deferred tools while keeping direct tools stable."""

    def __init__(
        self,
        *args: Any,
        defer_mcp_tools: bool = True,
        **kwargs: Any,
    ) -> None:
        self._tool_search_guard = threading.RLock()
        self._turn_activations: dict[tuple[str, str], set[str]] = {}
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
                "Search registered tools by capability. Deferred matches become available on the next model step "
                "for this turn only. Matches reported as already in your tool list can be called directly, so do not "
                "search for them again. "
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
            supports_parallel_tool_calls=True,
            exposure=ToolExposure.DIRECT,
        )

    def _search_tools(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("tool search query must not be empty")
        limit = max(1, min(20, int(arguments.get("limit", 5))))

        key = (context.session_id, context.turn_id)
        with self._tool_search_guard:
            activated_before = set(self._turn_activations.get(key, ()))
        exposed = self._current_step_tool_names(context.session_id, context.turn_id)

        # Search everything the model can call, not only the deferred tools. A model that looks up a
        # tool it already holds otherwise gets an empty or unrelated list, decides the tool does not
        # exist and searches again; one real run spent a fifth of its steps that way.
        candidates: dict[str, AgentTool] = {
            tool.name: tool
            for tool in self.tools.all()
            if tool.name != "tool_search" and tool.exposure in (ToolExposure.DIRECT, ToolExposure.DEFERRED)
        }
        scored: list[tuple[int, str, AgentTool]] = []
        for tool in candidates.values():
            score = _tool_match_score(tool, query)
            if score > 0:
                scored.append((score, tool.name, tool))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matches = tuple(item[2] for item in scored[:limit])
        # The step's frozen tool surface is the authority on what is callable right now; the registry
        # flags only stand in when no step is captured.
        available = tuple(
            tool.name for tool in matches
            if tool.name in activated_before
            or (tool.name in exposed if exposed is not None else tool.exposure is ToolExposure.DIRECT)
        )
        names = tuple(tool.name for tool in matches if tool.name not in available)
        if names:
            with self._tool_search_guard:
                self._turn_activations.setdefault(key, set()).update(names)

        records = [
            {
                "name": tool.name,
                "description": tool.description[:800],
                "effect": tool.effect.value,
                "source": "active" if tool.name in available else "deferred",
            }
            for tool in matches
        ]
        sentences = []
        if names:
            sentences.append("Tools activated for the next model step: " + ", ".join(names))
        if available:
            sentences.append("Already in your tool list, call them directly without searching again: " + ", ".join(available))
        content = ". ".join(sentences) if sentences else (
            f"No tools matched: {query}. Tools in your tool list are called by name; "
            "search only finds tools that are not in it yet."
        )
        return ToolResult(
            ok=True,
            content=content,
            data={
                "query": query,
                "count": len(records),
                "activated": list(names),
                "already_available": list(available),
                "tools": records,
            },
        )

    def _current_step_tool_names(self, session_id: str, turn_id: str) -> frozenset[str] | None:
        """Names the model can call in this turn's newest captured step, or None when none is captured."""
        with self._captured_steps_guard:
            steps = [step for key, step in self._captured_steps.items() if key[0] == session_id and key[1] == turn_id]
        if not steps:
            return None
        newest = max(steps, key=lambda step: step.model_step)
        return frozenset(tool.name for tool in newest.tool_router.all())

    def _activation_names(self, session: AgentSession) -> tuple[str, ...]:
        key = (session.session_id, session.current_turn_id)
        with self._tool_search_guard:
            return tuple(sorted(self._turn_activations.get(key, set())))

    def _activate_deferred_name(self, session_id: str, turn_id: str, tool_name: str) -> None:
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

        # Preserve the exact Step-scoped execution authority produced by lower
        # runtime layers (especially ConfiguredMCPRuntime's McpBinding). Tool
        # search may change exposure and schema pressure, but it must never swap
        # an exact handler for a long-lived registry projection.
        scoped_tools: dict[str, AgentTool] = {}
        for tool in step.tool_router.all():
            current = tool
            if (
                self.defer_mcp_tools
                and current.name.startswith("mcp.")
                and current.exposure is ToolExposure.DIRECT
            ):
                current = replace(current, exposure=ToolExposure.DEFERRED)
            scoped_tools[current.name] = current

        # Add deferred/non-visible registry tools for discovery. Existing Step
        # tools always win. Stale MCP compatibility projections are metadata only
        # and must not become executable authority for this Step.
        for tool in self.tools.all():
            if tool.name in scoped_tools:
                continue
            if str(tool.binding_key or "").startswith("mcp-binding:"):
                continue
            scoped_tools[tool.name] = tool

        base_router = ToolRegistry(tuple(scoped_tools.values())).router(
            activated_names=activations,
        )
        frozen_limits = step.request_state.context_limits
        limits = (
            frozen_limits
            if step.request_state.captured and frozen_limits is not None
            else resolve_context_limits(self, session)
        )
        # Schema projection preserves the Step's capability surface. The unified
        # request budget handles history pressure with the entire schema included.
        plan = plan_tool_schema_pressure(
            base_router,
            max_schema_tokens=schema_token_budget(
                limits.input_budget_tokens,
                conversation_tokens=self._conversation_pressure(session),
            ),
            pinned_names=activations,
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

    def _conversation_pressure(self, session: AgentSession) -> int:
        """Canonical history size, restated in the provider's token accounting.

        Deliberately excludes tool schemas: this is the number the schema budget
        is being weighed against, so counting definitions on both sides would
        count the same definitions twice when choosing annotation compression.
        """
        raw = estimate_tokens(tuple(session.messages))
        calibration = self.estimator_calibration(session.session_id)
        return int(math.ceil(raw * calibration))

    def _request_metadata(self, session, step):
        extra = super()._request_metadata(session, step)
        plan = self.tool_schema_plan(
            session.session_id,
            session.current_turn_id,
            step.step_id,
        )
        if plan is None:
            return extra
        metadata = dict(extra)
        metadata["tool_schema_plan"] = plan
        return metadata

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
        # Activation bookkeeping is not execution authority. The parent runtime
        # must resume the original in-process captured StepContext; after restart
        # that authority is unavailable and resume fails closed.
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
            self._turn_schema_plan.clear()
        super().close()


__all__ = ["ToolSearchRuntime"]
