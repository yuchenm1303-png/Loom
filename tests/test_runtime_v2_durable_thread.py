from __future__ import annotations
from conftest import without_stickers

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    GoalStatus,
    PendingToolApproval,
    QueueItemState,
    ToolEffect,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AIMessage, MessageRole, ModelResponse, ModelUsage, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _runtime(tmp_path, responses=()):
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=ScriptedPlatform(responses),
        store=store,
        tools=loom_default_tools(),
    )
    return runtime, store


def test_durable_queue_survives_runtime_restart(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)

    first = runtime.enqueue_turn(session.session_id, "first queued turn")
    second = runtime.enqueue_turn(session.session_id, "second queued turn")

    restarted = AgentRuntime(
        platform=ScriptedPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    queued = restarted.list_queued_turns(session.session_id)

    assert [item.queue_id for item in queued] == [first.queue_id, second.queue_id]
    assert [item.text for item in queued] == ["first queued turn", "second queued turn"]
    assert all(item.state is QueueItemState.PENDING for item in queued)
    assert restarted.durable_state.path.is_file()


def test_completed_turn_auto_drains_durable_queue(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(
        tmp_path,
        [ModelResponse(text="first complete"), ModelResponse(text="queued complete")],
    )
    session = runtime.create_session("agent.fast", workspace_dir=project)
    queued = runtime.enqueue_turn(session.session_id, "do the queued follow-up")

    result = runtime.start_turn(session.session_id, "do the immediate work")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "queued complete"
    assert runtime.list_queued_turns(session.session_id) == ()
    dispatched = [
        event
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.QUEUE_DISPATCHED
    ]
    assert dispatched[-1].data["queue_id"] == queued.queue_id
    user_events = [
        event.data
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.USER_MESSAGE
    ]
    assert [event["source"] for event in user_events[-2:]] == ["user", "queue"]


def test_stale_queue_claim_is_released_or_deduplicated(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    item = runtime.enqueue_turn(session.session_id, "queued")

    claimed = runtime.durable_state.claim_next(session.session_id, "orphan-turn")
    assert claimed is not None
    runtime.get_session(session.session_id)
    queued = runtime.list_queued_turns(session.session_id)
    assert queued[0].queue_id == item.queue_id
    assert queued[0].state is QueueItemState.PENDING

    claimed = runtime.durable_state.claim_next(session.session_id, "adopted-turn")
    assert claimed is not None
    session = store.load(session.session_id)
    session.current_turn_id = "adopted-turn"
    store.save(session)
    runtime.get_session(session.session_id)
    assert runtime.list_queued_turns(session.session_id) == ()


def test_recover_interrupted_preserves_durable_history_and_invalidates_execution_state(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.status = AgentStatus.RUNNING
    session.current_turn_id = "dead-turn"
    pending = ToolCall(call_id="missing-1", name="echo", arguments={"text": "x"})
    session.pending_tool_calls = [pending]
    session.pending_step_id = "step-dead"
    session.pending_bindings = {"missing-1": "stale-binding"}
    session.messages = [
        AIMessage(role=MessageRole.USER, content="run a tool"),
        AIMessage(
            role=MessageRole.ASSISTANT,
            content="",
            tool_calls=(pending,),
        ),
        AIMessage(
            role=MessageRole.TOOL,
            content="orphan",
            name="echo",
            tool_call_id="orphan-call",
        ),
    ]
    durable_messages = tuple(session.messages)
    store.save(session)

    result = runtime.recover_interrupted(session.session_id)
    restored = store.load(session.session_id)

    assert result.status is AgentStatus.INTERRUPTED
    assert tuple(restored.messages) == durable_messages
    assert restored.pending_tool_calls == []
    assert restored.pending_step_id == ""
    assert restored.pending_bindings == {}
    assert restored.pending_approval is None
    assert not [
        event
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.HISTORY_REPAIRED
    ]
    interrupted = [
        event
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_INTERRUPTED
    ]
    assert len(interrupted) == 1
    assert interrupted[0].data["previous_status"] == AgentStatus.RUNNING.value
    assert interrupted[0].data["invalidated_pending_call_ids"] == ["missing-1"]
    assert interrupted[0].data["invalidated_binding_ids"] == ["missing-1"]
    assert interrupted[0].data["history_projection"] == {
        "inserted_aborted_outputs": 1,
        "removed_orphan_outputs": 1,
        "removed_duplicate_outputs": 0,
        "persisted": False,
    }


def test_recover_waiting_approval_fails_closed_but_preserves_product_queue(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    queued = runtime.enqueue_turn(session.session_id, "do this later")
    call = ToolCall(call_id="approve-1", name="echo", arguments={"text": "x"})
    session = store.load(session.session_id)
    session.status = AgentStatus.WAITING_APPROVAL
    session.current_turn_id = "approval-turn"
    session.pending_tool_calls = [call]
    session.pending_step_id = "approval-step"
    session.pending_bindings = {"approve-1": "process-local-binding"}
    session.pending_approval = PendingToolApproval(
        call_id="approve-1",
        tool_name="echo",
        arguments={"text": "x"},
        effect=ToolEffect.MUTATING,
        reason="mutating tool requires approval",
    )
    store.save(session)

    result = runtime.recover_interrupted(session.session_id)
    restored = store.load(session.session_id)

    assert result.status is AgentStatus.INTERRUPTED
    assert restored.pending_approval is None
    assert restored.pending_tool_calls == []
    assert restored.pending_step_id == ""
    assert restored.pending_bindings == {}
    assert [item.queue_id for item in runtime.list_queued_turns(session.session_id)] == [queued.queue_id]
    interrupted = [
        event
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_INTERRUPTED
    ][-1]
    assert interrupted.data["previous_status"] == AgentStatus.WAITING_APPROVAL.value
    assert interrupted.data["invalidated_approval_call_id"] == "approve-1"


def test_recover_interrupted_is_idempotent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.status = AgentStatus.RUNNING
    session.current_turn_id = "dead-turn"
    store.save(session)

    first = runtime.recover_interrupted(session.session_id)
    second = runtime.recover_interrupted(session.session_id)

    assert first.status is AgentStatus.INTERRUPTED
    assert second.status is AgentStatus.INTERRUPTED
    assert len([
        event
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_INTERRUPTED
    ]) == 1


def test_goal_is_durable_and_budget_limited_by_real_model_usage(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, _store = _runtime(
        tmp_path,
        [
            ModelResponse(
                text="worked on it",
                usage=ModelUsage(input_tokens=4, output_tokens=3, total_tokens=7),
            )
        ],
    )
    session = runtime.create_session("agent.fast", workspace_dir=project)
    runtime.set_goal(session.session_id, "finish the refactor", token_budget=5)

    result = runtime.start_turn(session.session_id, "make progress")
    goal = runtime.get_goal(session.session_id)

    assert result.status is AgentStatus.COMPLETED
    assert goal is not None
    assert goal.tokens_used == 7
    assert goal.status is GoalStatus.BUDGET_LIMITED

    restarted = AgentRuntime(
        platform=ScriptedPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    restored = restarted.get_goal(session.session_id)
    assert restored is not None
    assert restored.objective == "finish the refactor"
    assert restored.status is GoalStatus.BUDGET_LIMITED


def test_active_goal_can_continue_after_runtime_restart(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, _store = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    runtime.set_goal(session.session_id, "implement durable recovery")

    platform = ScriptedPlatform([ModelResponse(text="continued successfully")])
    restarted = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    result = restarted.continue_goal(session.session_id)

    assert result.status is AgentStatus.COMPLETED
    assert without_stickers(result.final_text) == "continued successfully"
    request = platform.requests[0][1]
    user_messages = [message for message in request.messages if message.role is MessageRole.USER]
    assert "implement durable recovery" in str(user_messages[-1].content)
    assert restarted.get_goal(session.session_id).status is GoalStatus.ACTIVE


def test_model_can_complete_goal_and_stop_future_continuations(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="goal-complete",
                        name="mark_thread_goal",
                        arguments={"status": "complete"},
                    ),
                )
            ),
            ModelResponse(text="Goal is complete."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session("agent.fast", workspace_dir=project)
    runtime.set_goal(session.session_id, "finish one durable unit")

    result = runtime.continue_goal(session.session_id, max_turns=3)

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "Goal is complete."
    assert runtime.get_goal(session.session_id).status is GoalStatus.COMPLETE
    assert len(platform.requests) == 2
    assert AgentEventKind.TOOL_APPROVAL_REQUIRED not in [
        event.kind for event in store.events(session.session_id)
    ]
