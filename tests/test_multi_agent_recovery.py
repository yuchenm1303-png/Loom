from __future__ import annotations

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    SandboxManager,
    SandboxPolicy,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse


class ChildPlatform:
    def __init__(self) -> None:
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        last = request.messages[-1]
        assert last.role is MessageRole.USER
        if last.content == "initial child task":
            return ModelResponse(text="initial child result")
        if last.content == "durable follow-up":
            return ModelResponse(text="recovered follow-up result")
        raise AssertionError(f"unexpected child input: {last.content!r}")


def _runtime(state_root, platform, *, max_agents_per_tree=4):
    return AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(state_root),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        max_agents_per_tree=max_agents_per_tree,
    )


def test_agent_graph_and_child_queue_survive_runtime_restart(tmp_path):
    state_root = tmp_path / "state"
    workspace = tmp_path / "project"
    workspace.mkdir()

    runtime1 = _runtime(state_root, ChildPlatform())
    parent = runtime1.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    child_snapshot = runtime1.agent_control.spawn(
        parent.session_id,
        "initial child task",
        history_mode="none",
        background=False,
    )
    child_id = child_snapshot.node.session_id
    runtime1.agent_control.send(
        parent.session_id,
        child_id,
        "durable follow-up",
        wake=False,
    )
    assert runtime1.durable_state.pending_count(child_id) == 1
    runtime1.close()

    runtime2 = _runtime(state_root, ChildPlatform())
    restored = runtime2.agent_control.list_tree(parent.session_id, include_closed=False)
    assert len(restored) == 1
    assert restored[0].node.session_id == child_id
    assert restored[0].queue_depth == 1
    assert restored[0].execution_running is False

    result = runtime2.run_queued(child_id, max_turns=1)
    assert result is not None
    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "recovered follow-up result"
    assert runtime2.durable_state.pending_count(child_id) == 0
    persisted = runtime2.agent_graph.get(child_id)
    assert persisted is not None
    assert persisted.parent_session_id == parent.session_id
    runtime2.close()


def test_agent_tree_limit_is_enforced_without_privilege_changes(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime = _runtime(tmp_path / "state", ChildPlatform(), max_agents_per_tree=1)
    parent = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    first = runtime.agent_control.spawn(
        parent.session_id,
        "initial child task",
        history_mode="none",
        background=False,
    )
    child = runtime.store.load(first.node.session_id)
    assert child.permission_mode is parent.permission_mode
    assert child.workspace_dir == parent.workspace_dir

    try:
        runtime.agent_control.spawn(
            parent.session_id,
            "initial child task",
            history_mode="none",
            background=False,
        )
    except RuntimeError as exc:
        assert "agent tree limit" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("agent tree limit should reject the second active child")
    runtime.close()
