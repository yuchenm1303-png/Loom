from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from app.ai import AIMessage, MessageRole

from .agent_control import AgentControl
from .agent_graph import AgentGraphStore, AgentHistoryMode
from .context_runtime import ContextAgentRuntime
from .context_state import WorldStateEnvelope, compaction_split_index
from .history import repair_tool_history
from .multi_agent_tools import multi_agent_tools


class _RuntimeAgentControl(AgentControl):
    """AgentControl tuned for calls originating from an active model tool step."""

    def _inherited_history(self, parent, *, mode, recent_messages):
        # When spawn_agent itself is executing, the parent transcript ends with the
        # assistant tool call but its ToolResult cannot exist yet. Never copy that
        # incomplete control-plane call into the child; inherit only durable history.
        source = list(parent.messages)
        if (
            source
            and source[-1].role is MessageRole.ASSISTANT
            and source[-1].tool_calls
        ):
            source.pop()
        if mode is AgentHistoryMode.NONE:
            return []
        repaired = repair_tool_history(
            source,
            max_tool_result_chars=self.runtime.limits.max_tool_result_chars,
        )
        messages = tuple(repaired.messages)
        if mode is AgentHistoryMode.ALL:
            return list(messages)
        keep = max(2, min(80, int(recent_messages)))
        if len(messages) <= keep:
            return list(messages)
        split = compaction_split_index(messages, keep_recent=keep)
        return list(messages[split:] if split > 0 else messages[-keep:])

    def shutdown(self, *, wait: bool = False) -> None:
        # Futures are not durable, but active child turns own normal Loom
        # cancellation tokens. Request runtime cancellation before shutting the
        # executor down so running children do not outlive the control plane.
        with self._lock:
            session_ids = tuple(self._futures)
        for session_id in session_ids:
            try:
                self.runtime.cancel(session_id)
            except Exception:
                pass
        super().shutdown(wait=wait)


class MultiAgentRuntime(ContextAgentRuntime):
    """Runtime v2 stack with durable independent sub-agent threads.

    Sub-agents are ordinary Loom sessions, not nested model calls. Graph topology
    is persisted in the same SQLite runtime database used by goals/queues, while
    in-process futures are intentionally ephemeral. Every child inherits the
    parent's workspace and permission mode, and child tools continue through the
    normal permission, sandbox, process, patch, context, and recovery layers.
    """

    def __init__(
        self,
        *args,
        agent_graph: AgentGraphStore | None = None,
        max_agent_workers: int = 4,
        max_agents_per_tree: int = 16,
        max_agent_depth: int = 4,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.agent_graph = agent_graph or AgentGraphStore(self.store.root.parent)
        self.agent_control = _RuntimeAgentControl(
            self,
            self.agent_graph,
            max_workers=max_agent_workers,
            max_agents_per_tree=max_agents_per_tree,
            max_depth=max_agent_depth,
        )
        for tool in multi_agent_tools(self.agent_control):
            if self.tools.get(tool.name) is None:
                self.tools.register(tool)

    def close(self) -> None:
        try:
            self.agent_control.shutdown(wait=False)
        finally:
            super().close()

    def _agent_tree_state(self, session_id: str) -> dict[str, Any]:
        root_session_id = self.agent_graph.root_for_session(session_id)
        current_node = self.agent_graph.get(session_id)
        agents: list[dict[str, Any]] = []
        for snapshot in self.agent_control.list_tree(session_id, include_closed=False):
            agents.append(
                {
                    "session_id": snapshot.node.session_id,
                    "parent_session_id": snapshot.node.parent_session_id,
                    "role": snapshot.node.role,
                    "relation_status": snapshot.node.relation_status.value,
                    "session_status": snapshot.session_status.value,
                    "queue_depth": snapshot.queue_depth,
                    "execution_running": snapshot.execution_running,
                }
            )
        current: dict[str, Any]
        if current_node is None:
            current = {
                "session_id": session_id,
                "role": "root",
                "parent_session_id": None,
            }
        else:
            current = {
                "session_id": current_node.session_id,
                "role": current_node.role,
                "parent_session_id": current_node.parent_session_id,
            }
        return {
            "root_session_id": root_session_id,
            "current": current,
            "active_sub_agents": len(agents),
            "agents": agents,
        }

    def _context_envelope(self, session, step) -> WorldStateEnvelope:
        base = super()._context_envelope(session, step)
        payload = copy.deepcopy(base.payload)
        state = payload.get("state")
        if not isinstance(state, dict):  # pragma: no cover - context contract
            raise RuntimeError("runtime state envelope is missing state")
        state["agent_tree"] = self._agent_tree_state(session.session_id)
        canonical_state = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical_state.encode("utf-8")).hexdigest()
        payload["state_digest"] = digest
        text = (
            "LOOM_RUNTIME_STATE v1\n"
            "This runtime state is authoritative for the current model step. "
            "Do not infer broader filesystem, process, network, approval, or sub-agent permissions "
            "than stated here.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        )
        return WorldStateEnvelope(digest=digest, payload=payload, text=text)


__all__ = ["MultiAgentRuntime"]
