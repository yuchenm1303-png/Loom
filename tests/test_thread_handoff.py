"""An ordinary conversation can hand off without copy/paste or duplicate execution."""
import threading
import time

import pytest

from app.ai import MessageRole, ModelResponse, ToolCall
from app.agent_runtime.tools import ToolContext
from app.thread_handoff import ThreadHandoffs
from test_thread_branches import branch_service, wait_for_idle

PACKET = {"title": "接续修复", "task": "Continue fixing the parser and verify the result.", "summary": "Parser implemented; regression checks remain.",
          "completed": ["Read current parser"], "nextSteps": ["Run the regression checks"], "evidence": ["src/parser.py"], "display": "window"}


def settled(service, child_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = service.thread_handoffs.status(child_id)["incoming"]
        worker = service.thread_handoffs.workers.get(child_id)
        if value["state"] in {"completed", "failed", "interrupted", "approval"} and not (worker and worker.is_alive()) and not service._is_active(child_id):
            return value
        time.sleep(.02)
    raise AssertionError(service.thread_handoffs.status(child_id))


def test_handoff_inherits_authority_and_model_without_copying_history(branch_service):
    service, runtime, store, workspace = branch_service
    parent = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    source = store.load(parent)
    source.system_prompt = "A custom source policy."
    source.reasoning_kind, source.reasoning_value = "openai-effort", "high"
    source.communication_language = "zh"
    source.tool_selection = ["handoff_thread"]
    source.weave = True
    store.save(source)
    first = service.thread_handoffs.create(parent, "same-request", PACKET)
    child_id = first["threadId"]
    result = settled(service, child_id)
    assert result["state"] == "completed", result
    child = store.load(child_id)
    assert child.workspace_dir == source.workspace_dir and child.permission_mode == source.permission_mode
    assert child.system_prompt == source.system_prompt and child.profile_id == source.profile_id
    assert child.reasoning_value == "high" and child.tool_selection == source.tool_selection
    assert child.weave and child.communication_language == "zh"
    assert child.messages[0].content.startswith("Continue the user's work")
    assert "src/parser.py" in child.messages[0].content
    assert "budgets" not in service.thread_library.read(child_id)  # no new budget grant
    again = service.thread_handoffs.create(parent, "same-request", PACKET)
    assert again["threadId"] == child_id and len(service.thread_list({})["threads"]) == 2
    assert service.thread_read({"threadId": parent})["thread"]["handoffs"][-1]["id"] == child_id
    assert service.thread_read({"threadId": child_id})["thread"]["handoff"]["sourceThreadId"] == parent
    with pytest.raises(ValueError, match="different content"):
        service.thread_handoffs.create(parent, "same-request", {**PACKET, "task": "Something else"})


def test_real_model_tool_waits_for_source_turn_before_starting_receiver(branch_service):
    service, runtime, store, workspace = branch_service
    parent = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    calls = []
    class Platform:
        def execute_chat(self, _profile, request):
            if not request.tools:
                return ModelResponse(text="Source task")
            text = "\n".join(str(message.content) for message in request.messages)
            if "Continue the user's work in this new Loom conversation" in text:
                assert not service._is_active(parent)
                calls.append("receiver")
                return ModelResponse(text="Receiver continued the task.")
            if any(message.role is MessageRole.TOOL for message in request.messages):
                calls.append("source-finish")
                return ModelResponse(text="交接已建立，我在此结束。")
            calls.append("source-tool")
            return ModelResponse(tool_calls=(ToolCall(call_id="handoff-1", name="handoff_thread", arguments=PACKET),))
    runtime.set_session_model(parent, Platform())
    service.turn_start({"threadId": parent, "input": "Open another window and continue there."})
    wait_for_idle(service, parent)
    outgoing = service.thread_handoffs.status(parent)["outgoing"]
    assert len(outgoing) == 1
    assert settled(service, outgoing[0]["threadId"])["state"] == "completed"
    assert calls == ["source-tool", "source-finish", "receiver"]


def test_preparation_failure_retry_is_compare_and_swap_and_keeps_the_child(branch_service, monkeypatch):
    service, _, _, workspace = branch_service
    parent = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    prepare = service._prepare_turn
    monkeypatch.setattr(service, "_prepare_turn", lambda _: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    created = service.thread_handoffs.create(parent, "retryable", PACKET)
    failed = settled(service, created["threadId"])
    assert failed["state"] == "failed" and "provider unavailable" in failed["error"]
    monkeypatch.setattr(service, "_prepare_turn", prepare)
    expected = failed["turnId"]
    results = []
    workers = [threading.Thread(target=lambda: results.append(service.thread_handoffs.retry(created["threadId"], expected))) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert len({value["turnId"] for value in results}) == 1
    assert settled(service, created["threadId"])["state"] == "completed"
    replay = service.thread_handoffs.retry(created["threadId"], expected)
    assert replay["turnId"] == results[0]["turnId"]
    assert len(service.thread_list({})["threads"]) == 2


def test_crash_after_session_create_reuses_the_reserved_identity(branch_service, monkeypatch):
    service, runtime, _, workspace = branch_service
    parent = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    original = runtime.create_session
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("crash after durable session creation")
    monkeypatch.setattr(runtime, "create_session", interrupted)
    with pytest.raises(OSError):
        service.thread_handoffs.create(parent, "crash-gap", PACKET)
    reserved = service.thread_library.read(parent)["handoffs"][-1]["threadId"]
    monkeypatch.setattr(runtime, "create_session", original)
    receipt = service.thread_handoffs.status(parent)["outgoing"][-1]
    assert receipt["created"] is False and receipt["state"] == "interrupted"
    created = service.thread_handoffs.retry(reserved, receipt["turnId"], parent)
    assert created["threadId"] == reserved
    assert settled(service, reserved)["state"] == "completed"
    restarted = ThreadHandoffs(service)
    assert restarted.create(parent, "crash-gap", PACKET)["threadId"] == reserved
    assert len(service.thread_list({})["threads"]) == 2


def test_a_crashed_running_receiver_is_reported_as_interrupted(branch_service):
    from app.agent_runtime import AgentStatus
    service, _, store, workspace = branch_service
    source = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    receipt = service.thread_handoffs.create(source, "crashed-running", PACKET)
    settled(service, receipt["threadId"])
    record = service.thread_library.read(receipt["threadId"])["incomingHandoff"]
    record["state"] = "running"
    service.thread_library.write(receipt["threadId"], {"incomingHandoff": record})
    child = service._load(receipt["threadId"])
    child.status = AgentStatus.RUNNING
    store.save(child)
    assert ThreadHandoffs(service).status(child.session_id)["incoming"]["state"] == "interrupted"


def test_receiver_does_not_spawn_an_unbounded_chain_from_inherited_handoff(branch_service):
    service, _, _, workspace = branch_service
    parent = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    record = service.thread_handoffs.create(parent, "first", PACKET)
    settled(service, record["threadId"])
    with pytest.raises(ValueError, match="new user turn"):
        service.thread_handoffs.create(record["threadId"], "automatic-chain", PACKET)
    service.turn_start({"threadId": record["threadId"], "input": "Now explicitly hand off again."})
    wait_for_idle(service, record["threadId"])
    next_record = service.thread_handoffs.create(record["threadId"], "new-user-request", PACKET)
    assert next_record["threadId"] != record["threadId"]
    settled(service, next_record["threadId"])


def test_invalid_packets_and_archived_sources_do_not_create_conversations(branch_service):
    service, runtime, _, workspace = branch_service
    parent = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    for packet in [{**PACKET, "summary": ""}, {**PACKET, "display": "javascript:"}, {**PACKET, "nextSteps": [5]}]:
        with pytest.raises(ValueError): service.thread_handoffs.create(parent, "invalid", packet)
    with pytest.raises(ValueError): runtime.create_session("test", session_id="../../escape")
    assert len(service.thread_list({})["threads"]) == 1
    service.thread_archive({"threadId": parent})
    with pytest.raises(ValueError, match="Restore"):
        service.thread_handoffs.create(parent, "archived", PACKET)


def test_a_project_handoff_pauses_its_run_and_prevents_a_new_milestone(tmp_path):
    from app.app_server_thread_management import ManagedStreamingLoomAppServerService
    from app.project_runner.service import ProjectServiceMixin
    from app.project_runner import program
    from test_project_runner import build
    class Service(ProjectServiceMixin, ManagedStreamingLoomAppServerService):
        pass
    service, runtime, _, workspace, source = build(tmp_path, [], service_class=Service)
    try:
        current = program.create(service, {"operationId": "program-for-handoff", "threadId": source,
            "objective": "Implement the parser", "tokenBudget": 10000, "maxEpisodes": 40})
        run_id = current["currentRunId"]
        receiver = service.thread_start({"workspace": str(workspace), "permissionMode": service._load(source).permission_mode.value})["thread"]["id"]
        service.thread_handoffs._pause_source(source, receiver)
        assert program.get(service.project_store, current["id"])["handoffHold"] == receiver
        assert service.project_store.get(run_id)["control"] == "paused"
        assert not program.advance(service)
        run = service.project_store.get(run_id)
        service.project_control({"runId": run_id, "operationId": "explicit-source-resume", "expectedRevision": run["revision"]}, "resume")
        assert not program.get(service.project_store, current["id"]).get("handoffHold")
        # An integrated milestone resumes the held program, while retaining the
        # project's normal revision and operation validation.
        held = program.get(service.project_store, current["id"])
        held["handoffHold"] = receiver
        program._save(service.project_store, held)
        with service.project_store.transaction() as db:
            run = service.project_store._read(db, run_id)
            run["state"] = "integrated"
            service.project_store.save(db, run, "test_integrated")
        with pytest.raises(ValueError, match="stale"):
            service.project_control({"runId": run_id, "operationId": "stale-resume", "expectedRevision": run["revision"] - 1}, "resume")
        assert program.get(service.project_store, current["id"])["handoffHold"] == receiver
        service.project_control({"runId": run_id, "operationId": "resume-held-program", "expectedRevision": run["revision"]}, "resume")
        assert not program.get(service.project_store, current["id"]).get("handoffHold")
    finally:
        runtime.close()


def test_receiver_archived_while_source_is_finishing_does_not_start(branch_service):
    service, _, _, workspace = branch_service
    source = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
    service._active_sessions.add(source)
    try:
        record = service.thread_handoffs.create(source, "archive-before-start", PACKET)
        service.thread_archive({"threadId": record["threadId"]})
    finally:
        service._active_sessions.discard(source)
    result = settled(service, record["threadId"])
    assert result["state"] == "failed" and "archived" in result["error"]
    assert not service.store.load(record["threadId"]).messages


def test_handoff_pauses_the_source_goal_without_resetting_or_copying_budget(tmp_path):
    from app.app_server_thread_management import ManagedStreamingLoomAppServerService
    from app.agent_runtime import GoalStatus
    from app.project_runner.service import ProjectServiceMixin
    from app.project_runner import goal
    from test_project_runner import build
    class Service(ProjectServiceMixin, ManagedStreamingLoomAppServerService):
        pass
    service, runtime, _, workspace, source = build(tmp_path, [], service_class=Service)
    try:
        goal.start(service, source, "Finish the research", 5000)
        record = service.weave_goals.get(source)
        record["tokens"] = 1234
        service.weave_goals.put(source, record)
        receiver = service.thread_start({"workspace": str(workspace), "permissionMode": service._load(source).permission_mode.value})["thread"]["id"]
        service.thread_handoffs._pause_source(source, receiver)
        assert runtime.get_goal(source).status is GoalStatus.PAUSED
        assert service.weave_goals.get(source)["tokens"] == 1234
        assert service.weave_goals.get(source)["tokenBudget"] == 5000
        assert runtime.get_goal(receiver) is None and service.weave_goals.get(receiver) is None
        assert not goal.advance(service)
    finally:
        runtime.close()


def test_a_handed_off_provider_failure_cannot_autorestart_source_or_receiver(tmp_path):
    from types import SimpleNamespace
    from app.app_server_thread_management import ManagedStreamingLoomAppServerService
    from app.agent_runtime import AgentEventKind
    from app.project_runner.service import ProjectServiceMixin
    from app.project_runner import goal
    from test_project_runner import build
    class Service(ProjectServiceMixin, ManagedStreamingLoomAppServerService):
        pass
    service, runtime, _, _, source = build(tmp_path, [], service_class=Service)
    try:
        data = {"error": "provider timeout", "provider_failure": True, "retryable": True}
        goal.observe_failure(service, SimpleNamespace(kind=AgentEventKind.TURN_FAILED, session_id=source, turn_id="ordinary-turn", data=data))
        assert service.weave_retries.get(source)["turn"] == "ordinary-turn"
        service.weave_retries.take(source)
        service.thread_library.write(source, {"handoffs": [{"sourceTurnId": "source-handoff-turn"}]})
        goal.observe_failure(service, SimpleNamespace(kind=AgentEventKind.TURN_FAILED, session_id=source, turn_id="source-handoff-turn", data=data))
        assert service.weave_retries.get(source) is None
        service.thread_library.write(source, {"handoffs": [], "incomingHandoff": {"turnId": "receiver-handoff-turn"}})
        goal.observe_failure(service, SimpleNamespace(kind=AgentEventKind.TURN_FAILED, session_id=source, turn_id="receiver-handoff-turn", data=data))
        assert service.weave_retries.get(source) is None
    finally:
        runtime.close()
