from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from app.ai import AIMessage

from .agent_graph import (
    AgentGraphStore,
    AgentHistoryMode,
    AgentNode,
    AgentRelationStatus,
)
from .context_state import compaction_split_index
from .contracts import AgentRunResult, AgentSession, AgentStatus
from .history import repair_tool_history


@dataclass(frozen=True, slots=True)
class AgentExecutionSnapshot:
    node: AgentNode
    session_status: AgentStatus
    final_text: str
    error: str
    queue_depth: int
    execution_running: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.node.session_id,
            "parent_session_id": self.node.parent_session_id,
            "root_session_id": self.node.root_session_id,
            "role": self.node.role,
            "history_mode": self.node.history_mode.value,
            "relation_status": self.node.relation_status.value,
            "session_status": self.session_status.value,
            "final_text": self.final_text,
            "error": self.error,
            "queue_depth": self.queue_depth,
            "execution_running": self.execution_running,
        }


class AgentControl:
    """Control plane for a durable tree of independent Loom agent sessions.

    Graph/session/queue state is durable. Python futures are deliberately
    ephemeral execution handles: after a process restart the child sessions and
    queued work remain recoverable, but an old ThreadPool Future is not restored.
    """

    def __init__(
        self,
        runtime: Any,
        graph: AgentGraphStore,
        *,
        max_workers: int = 4,
        max_agents_per_tree: int = 16,
        max_depth: int = 4,
    ) -> None:
        self.runtime = runtime
        self.graph = graph
        self.max_workers = max(1, int(max_workers))
        self.max_agents_per_tree = max(1, int(max_agents_per_tree))
        self.max_depth = max(1, int(max_depth))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="loom-agent",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future[AgentRunResult | None]] = {}
        self._closed = False

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("agent control plane is closed")

    def _active_parent(self, parent_session_id: str) -> AgentSession:
        parent = self.runtime.store.load(parent_session_id)
        node = self.graph.get(parent.session_id)
        if node is not None and node.relation_status is AgentRelationStatus.CLOSED:
            raise RuntimeError("closed sub-agent cannot spawn another agent")
        return parent

    def _inherited_history(
        self,
        parent: AgentSession,
        *,
        mode: AgentHistoryMode,
        recent_messages: int,
    ) -> list[AIMessage]:
        if mode is AgentHistoryMode.NONE:
            return []
        repaired = repair_tool_history(
            parent.messages,
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

    @staticmethod
    def _child_system_prompt(parent: AgentSession, role: str) -> str:
        role_name = str(role or "worker").strip() or "worker"
        return (
            f"{parent.system_prompt}\n\n"
            "You are an independent Loom sub-agent thread. "
            f"Your delegated role is: {role_name}. "
            "Work only on the task delegated to this thread. You share the parent workspace and permission mode, "
            "but your conversation state, turns, goals, queue, and failures are independent. "
            "Report concrete results rather than pretending the parent already knows them."
        )

    def spawn(
        self,
        parent_session_id: str,
        task: str,
        *,
        role: str = "worker",
        history_mode: AgentHistoryMode | str = AgentHistoryMode.RECENT,
        recent_messages: int = 16,
        profile_id: str | None = None,
        background: bool = True,
    ) -> AgentExecutionSnapshot:
        self._assert_open()
        body = str(task or "").strip()
        if not body:
            raise ValueError("sub-agent task must not be empty")
        if len(body) > 200_000:
            raise ValueError("sub-agent task exceeds 200,000 characters")
        parent = self._active_parent(parent_session_id)
        if self.graph.depth(parent.session_id) >= self.max_depth:
            raise RuntimeError(f"agent tree depth limit reached ({self.max_depth})")
        if self.graph.active_count(parent.session_id) >= self.max_agents_per_tree:
            raise RuntimeError(
                f"active agent tree limit reached ({self.max_agents_per_tree})"
            )

        resolved_history = AgentHistoryMode(history_mode)
        child = self.runtime.create_session(
            str(profile_id or parent.profile_id),
            system_prompt=self._child_system_prompt(parent, role),
            workspace_dir=parent.workspace_dir,
            permission_mode=parent.permission_mode,
        )
        child.messages = self._inherited_history(
            parent,
            mode=resolved_history,
            recent_messages=recent_messages,
        )
        self.runtime.store.save(child)
        node = self.graph.add_child(
            parent_session_id=parent.session_id,
            child_session_id=child.session_id,
            role=role,
            history_mode=resolved_history,
        )

        if background:
            self._submit(child.session_id, lambda: self.runtime.start_turn(child.session_id, body))
        else:
            self.runtime.start_turn(child.session_id, body)
        return self.snapshot(parent.session_id, node.session_id)

    def _submit(self, session_id: str, call) -> Future[AgentRunResult | None]:
        with self._lock:
            existing = self._futures.get(session_id)
            if existing is not None and not existing.done():
                return existing
            future = self._executor.submit(call)
            self._futures[session_id] = future
            return future

    def send(
        self,
        caller_session_id: str,
        target_session_id: str,
        text: str,
        *,
        wake: bool = True,
    ) -> AgentExecutionSnapshot:
        self._assert_open()
        node = self.graph.assert_same_tree(caller_session_id, target_session_id)
        if node.relation_status is AgentRelationStatus.CLOSED:
            raise RuntimeError("target sub-agent is closed")
        item = self.runtime.enqueue_turn(node.session_id, text)
        if wake:
            session = self.runtime.store.load(node.session_id)
            if session.status not in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                self._submit(
                    node.session_id,
                    lambda: self.runtime.run_queued(node.session_id, max_turns=1),
                )
        _ = item
        return self.snapshot(caller_session_id, node.session_id)

    def wait(
        self,
        caller_session_id: str,
        target_session_id: str,
        *,
        timeout_seconds: float = 0.0,
    ) -> AgentExecutionSnapshot:
        self._assert_open()
        node = self.graph.assert_same_tree(caller_session_id, target_session_id)
        timeout = max(0.0, min(120.0, float(timeout_seconds)))
        with self._lock:
            future = self._futures.get(node.session_id)
        if future is not None and not future.done() and timeout > 0:
            try:
                future.result(timeout=timeout)
            except FutureTimeoutError:
                pass
        return self.snapshot(caller_session_id, node.session_id)

    def snapshot(
        self,
        caller_session_id: str,
        target_session_id: str,
    ) -> AgentExecutionSnapshot:
        node = self.graph.assert_same_tree(caller_session_id, target_session_id)
        session = self.runtime.store.load(node.session_id)
        with self._lock:
            future = self._futures.get(node.session_id)
            execution_running = future is not None and not future.done()
        queue_depth = self.runtime.durable_state.pending_count(node.session_id)
        return AgentExecutionSnapshot(
            node=node,
            session_status=session.status,
            final_text=session.final_text,
            error=session.error,
            queue_depth=queue_depth,
            execution_running=execution_running,
        )

    def list_tree(
        self,
        caller_session_id: str,
        *,
        include_closed: bool = True,
    ) -> tuple[AgentExecutionSnapshot, ...]:
        self._assert_open()
        nodes = self.graph.list_tree(caller_session_id, include_closed=include_closed)
        return tuple(
            self.snapshot(caller_session_id, node.session_id)
            for node in nodes
        )

    def close_agent(
        self,
        caller_session_id: str,
        target_session_id: str,
    ) -> tuple[AgentExecutionSnapshot, ...]:
        self._assert_open()
        target = self.graph.assert_same_tree(caller_session_id, target_session_id)
        to_close: list[AgentNode] = []
        stack = [target]
        while stack:
            node = stack.pop()
            to_close.append(node)
            stack.extend(self.graph.list_children(node.session_id, include_closed=False))

        snapshots: list[AgentExecutionSnapshot] = []
        for node in reversed(to_close):
            try:
                self.runtime.cancel(node.session_id)
            except Exception:
                pass
            process_store = getattr(self.runtime, "process_store", None)
            if process_store is not None:
                try:
                    process_store.terminate_session(node.session_id)
                except Exception:
                    pass
            with self._lock:
                future = self._futures.get(node.session_id)
                if future is not None:
                    future.cancel()
            self.graph.close(node.session_id)
            snapshots.append(self.snapshot(caller_session_id, node.session_id))
        return tuple(snapshots)

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._futures.values())
        for future in futures:
            future.cancel()
        self._executor.shutdown(wait=bool(wait), cancel_futures=True)


__all__ = ["AgentControl", "AgentExecutionSnapshot"]
