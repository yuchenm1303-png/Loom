from __future__ import annotations

import json
import threading

from app.agent_runtime import (
    AgentGraphStore,
    AgentRelationStatus,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ToolCall


class DelegationPlatform:
    def __init__(self) -> None:
        self.requests = []
        self._lock = threading.RLock()

    def execute_chat(self, profile_id, request):
        with self._lock:
            self.requests.append((profile_id, request))

        last = request.messages[-1]
        if last.role is MessageRole.USER and last.content == "Inspect delegated task":
            return ModelResponse(text="child-result: inspected")

        if last.role is MessageRole.TOOL and last.name == "spawn_agent":
            payload = json.loads(last.content)
            agent_id = payload["data"]["session_id"]
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="wait-child",
                        name="wait_agent",
                        arguments={"agent_id": agent_id, "timeout_seconds": 5},
                    ),
                )
            )

        if last.role is MessageRole.TOOL and last.name == "wait_agent":
            payload = json.loads(last.content)
            assert payload["data"]["final_text"] == "child-result: inspected"
            return ModelResponse(text="parent-result: child completed")

        if last.role is MessageRole.USER and last.content == "Delegate this work":
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="spawn-child",
                        name="spawn_agent",
                        arguments={
                            "task": "Inspect delegated task",
                            "role": "researcher",
                            "history_mode": "recent",
                        },
                    ),
                )
            )

        raise AssertionError(f"unexpected model request tail: {last.role.value} {last.name!r} {last.content!r}")


def _runtime(tmp_path):
    store = FileAgentSessionStore(tmp_path / "state")
    platform = DelegationPlatform()
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        max_agent_workers=2,
        max_agents_per_tree=4,
        max_agent_depth=3,
    )
    return runtime, store, platform


def test_model_can_spawn_wait_and_receive_independent_child_result(tmp_path):
    runtime, store, platform = _runtime(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()
    parent = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )
    parent.messages.extend(
        [
            # Stable prior context should be inherited, but the in-flight
            # spawn_agent call itself must never leak into child history.
            __import__("app.ai", fromlist=["AIMessage"]).AIMessage(
                role=MessageRole.USER, content="prior-context"
            ),
            __import__("app.ai", fromlist=["AIMessage"]).AIMessage(
                role=MessageRole.ASSISTANT, content="prior-answer"
            ),
        ]
    )
    store.save(parent)

    result = runtime.start_turn(parent.session_id, "Delegate this work")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "parent-result: child completed"
    tree = runtime.agent_control.list_tree(parent.session_id, include_closed=False)
    assert len(tree) == 1
    child_snapshot = tree[0]
    assert child_snapshot.node.role == "researcher"
    assert child_snapshot.session_status is AgentStatus.COMPLETED
    assert child_snapshot.final_text == "child-result: inspected"

    child = store.load(child_snapshot.node.session_id)
    assert child.workspace_dir == str(workspace.resolve())
    assert child.permission_mode is PermissionMode.WORKSPACE
    assert any(message.content == "prior-context" for message in child.messages)
    assert all(
        not (message.role is MessageRole.TOOL and "aborted" in str(message.content))
        for message in child.messages
    )

    parent_requests = [
        request for _, request in platform.requests
        if request.messages[-1].content != "Inspect delegated task"
    ]
    first_tools = {tool.name for tool in parent_requests[0].tools}
    assert {
        "spawn_agent",
        "send_agent_message",
        "wait_agent",
        "list_agents",
        "close_agent",
    }.issubset(first_tools)
    assert any(
        message.name == "loom_runtime_state" and '"agent_tree"' in str(message.content)
        for request in parent_requests
        for message in request.messages
    )

    reopened_graph = AgentGraphStore(store.root.parent)
    reopened = reopened_graph.get(child.session_id)
    assert reopened is not None
    assert reopened.parent_session_id == parent.session_id
    assert reopened.root_session_id == parent.session_id
    runtime.close()


def test_agent_messages_use_durable_queue_and_tree_boundary(tmp_path):
    runtime, store, _ = _runtime(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()
    parent = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    child = runtime.agent_control.spawn(
        parent.session_id,
        "Inspect delegated task",
        history_mode="none",
        background=False,
    )
    queued = runtime.agent_control.send(
        parent.session_id,
        child.node.session_id,
        "follow-up later",
        wake=False,
    )
    assert queued.queue_depth == 1
    assert runtime.durable_state.pending_count(child.node.session_id) == 1

    other_root = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    try:
        runtime.agent_control.send(
            other_root.session_id,
            child.node.session_id,
            "not allowed",
            wake=False,
        )
    except PermissionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("cross-tree agent messaging must be denied")

    closed = runtime.agent_control.close_agent(parent.session_id, child.node.session_id)
    assert closed[-1].node.relation_status is AgentRelationStatus.CLOSED
    persisted = runtime.agent_graph.get(child.node.session_id)
    assert persisted is not None
    assert persisted.relation_status is AgentRelationStatus.CLOSED
    runtime.close()
