from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from .contracts import AgentSession, AgentStatus, ToolEffect
from .mcp_configured_runtime import ConfiguredMCPRuntime
from .step import StepContext
from .tools import AgentTool, ToolContext, ToolExposure, ToolRegistry, ToolResult


class ToolSearchRuntime(ConfiguredMCPRuntime):
    """Runtime layer that discovers deferred tools on demand.

    Deferred activation is intentionally scoped to one active turn. The durable
    pending-approval record is sufficient to reconstruct a deferred tool if the
    process restarts while the user is deciding whether to approve it.
    """

    def __init__(
        self,
        *args: Any,
        defer_mcp_tools: bool = True,
        **kwargs: Any,
    ) -> None:
        self._tool_search_guard = threading.RLock()
        self._turn_activations: dict[tuple[str, str], set[str]] = {}
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
                "Matching tools become available on the next model step for this turn only. "
                "Use a concise capability query such as 'github create issue' or 'calendar events'."
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

    def _search_tools(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit", 5))
        matches = self.tools.search_deferred(query, limit=limit)
        names = tuple(tool.name for tool in matches)
        if names:
            key = (context.session_id, context.turn_id)
            with self._tool_search_guard:
                self._turn_activations.setdefault(key, set()).update(names)

        records = [
            {
                "name": tool.name,
                "description": tool.description[:800],
                "effect": tool.effect.value,
            }
            for tool in matches
        ]
        if records:
            content = "Deferred tools activated for the next model step: " + ", ".join(names)
        else:
            content = f"No deferred tools matched: {query}"
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
        tool = self.tools.get(tool_name)
        if tool is None or tool.exposure is not ToolExposure.DEFERRED:
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
        router = self.tools.router(activated_names=self._activation_names(session))
        return replace(
            step,
            tool_router=router,
            world_state=replace(
                step.world_state,
                tool_names=tuple(tool.name for tool in router.all()),
            ),
        )

    def start_turn(
        self,
        session_id: str,
        user_text: str,
        *,
        turn_id: str | None = None,
    ):
        self._clear_session_activations(session_id)
        result = super().start_turn(session_id, user_text, turn_id=turn_id)
        if result.status is not AgentStatus.WAITING_APPROVAL:
            self._clear_session_activations(session_id)
        return result

    def resume_approval(self, session_id: str, call_id: str, *, approved: bool):
        # Reconstruct the exact deferred pending tool before the parent runtime
        # rebuilds its immutable StepContext. This makes approval resume safe
        # even after a host restart erased turn-scoped activation memory.
        session = self.store.load(session_id)
        pending = session.pending_approval
        if pending is not None and pending.call_id == str(call_id or "").strip():
            self._activate_deferred_name(
                session.session_id,
                session.current_turn_id,
                pending.tool_name,
            )
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
        super().close()


__all__ = ["ToolSearchRuntime"]
