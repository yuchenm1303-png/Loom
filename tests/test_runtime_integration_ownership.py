"""Production construction must not change execution method ownership."""
import threading
import time
import sys

import pytest

from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime import context_budget
from app.app_server import LoomAppServerService
from app.app_server_streaming import StreamingLoomAppServerService
from app.ai import ModelResponse, ModelUsage, MessageRole
from app.ai.execution_control import ModelCancelled, check_cancelled


REMOVED_PATCHES = {
    "app.live_steering_contract", "app.live_steering_interrupt_contract",
    "app.agent_continuity_contract", "app.compaction_resilience_contract",
}


class Platform:
    def __init__(self):
        self.requests = []

    def execute_chat(self, _profile, request):
        self.requests.append(request)
        return ModelResponse(text="Done")


@pytest.mark.parametrize("service_class", [LoomAppServerService, StreamingLoomAppServerService])
def test_service_construction_does_not_replace_execution_methods(tmp_path, service_class):
    platform = Platform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    names = ("steer", "_consume_steering", "resume_steered_turn", "_before_turn_completed",
             "_track_goal_usage", "_model_system_prompt", "_commit_compaction_locked",
             "compact_context_with_model", "recover_turn_if_idle")
    try:
        before = {name: getattr(type(runtime), name, None) for name in names}
        first = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        first_step = runtime._build_step_context(first, next_model_step=False)
        first_prompt = runtime._model_system_prompt(first, first_step)
        runtime.start_turn(first.session_id, "Check")
        service_class(runtime=runtime, store=store, model="test", default_workspace=tmp_path)
        assert before == {name: getattr(type(runtime), name, None) for name in names}
        assert first_prompt == runtime._model_system_prompt(first, first_step)
        assert "LOOM_LIVE_STEERING_CONTRACT" in first_prompt
        custom = runtime.create_session("agent.fast", workspace_dir=tmp_path,
                                        system_prompt="CUSTOM [LOOM_LIVE_STEERING_CONTRACT]")
        custom_step = runtime._build_step_context(custom, next_model_step=False)
        assert runtime._model_system_prompt(custom, custom_step).count("LOOM_LIVE_STEERING_CONTRACT") == 1
        second = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(second.session_id, "Check")
        assert platform.requests[0].messages[0].content == platform.requests[1].messages[0].content
        for cls in type(runtime).__mro__:
            for name, method in vars(cls).items():
                assert getattr(method, "__module__", "") not in REMOVED_PATCHES, (cls, name)
        assert context_budget.prepare_context.__module__ == "app.agent_runtime.context_budget"
        assert all(type(finder).__module__ not in REMOVED_PATCHES for finder in sys.meta_path)
    finally:
        runtime.close()


def test_production_safe_handoff_accounts_goal_usage(tmp_path):
    class UsagePlatform(Platform):
        def execute_chat(self, _profile, request):
            self.requests.append(request)
            return ModelResponse(text="Done", usage=ModelUsage(input_tokens=4, output_tokens=3, total_tokens=7))

    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=UsagePlatform(), store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    LoomAppServerService(runtime=runtime, store=store, model="test", default_workspace=tmp_path)
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.set_goal(session.session_id, "Complete the authorized handoff", token_budget=100)
        runtime.durable_state.add_goal_usage(session.session_id, 5)
        session.usage = ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5)
        session.current_turn_id = "handoff"
        session.status = AgentStatus.RUNNING
        store.save(session)
        result = runtime.recover_turn_if_idle(session.session_id, "handoff")
        assert result.turn_id == "handoff" and result.status is AgentStatus.COMPLETED
        assert runtime.get_goal(session.session_id).tokens_used == 12
        assert type(runtime).recover_turn_if_idle.__module__ == "app.agent_runtime.durable_runtime"
    finally:
        runtime.close()


@pytest.mark.parametrize("pending", ["step", "binding", "approval"])
def test_production_safe_handoff_rejects_unresolved_authority(tmp_path, pending):
    platform = Platform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        session.current_turn_id = "handoff"
        session.status = AgentStatus.RUNNING
        if pending == "step":
            session.pending_step_id = "unknown-outcome"
        elif pending == "binding":
            session.pending_bindings = {"unknown-call": "persisted-binding"}
        else:
            session.status = AgentStatus.WAITING_APPROVAL
        store.save(session)
        with pytest.raises(RuntimeError):
            runtime.recover_turn_if_idle(session.session_id, "handoff")
        assert not platform.requests
        persisted = store.load(session.session_id)
        assert persisted.current_turn_id == "handoff" and persisted.status is session.status
        assert persisted.pending_step_id == session.pending_step_id
        assert persisted.pending_bindings == session.pending_bindings
    finally:
        runtime.close()


@pytest.mark.parametrize("with_service", [False, True])
def test_running_production_steering_is_identical_before_service_construction(tmp_path, with_service):
    class InterruptiblePlatform(Platform):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.cancelled = threading.Event()

        def execute_chat(self, _profile, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                self.started.set()
                try:
                    while True:
                        check_cancelled()
                        time.sleep(0.005)
                except ModelCancelled:
                    self.cancelled.set()
                    raise
            return ModelResponse(text="Replacement direction completed")

    platform = InterruptiblePlatform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    if with_service:
        StreamingLoomAppServerService(runtime=runtime, store=store, model="test", default_workspace=tmp_path)
    runtime.set_sticker_preferences({"frequency": 0})
    session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
    outcome = []

    def run():
        try:
            outcome.append(runtime.start_turn(session.session_id, "Initial direction"))
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert platform.started.wait(3)
        turn_id = store.load(session.session_id).current_turn_id
        receipt = runtime.steer(session.session_id, "Replacement direction", turn_id=turn_id, input_id="once")
        assert receipt["delivery"] == "model_replan_requested"
        assert platform.cancelled.wait(3)
        worker.join(3)
        assert not worker.is_alive()
        assert outcome and not isinstance(outcome[0], BaseException), outcome
        assert outcome[0].turn_id == turn_id
        retry = runtime.steer(session.session_id, "Replacement direction", turn_id=turn_id, input_id="once")
        assert retry["duplicate"] and retry["applied"]
        persisted = store.load(session.session_id)
        assert len([m for m in persisted.messages if m.role is MessageRole.USER and m.content == "Replacement direction"]) == 1
        assert len(platform.requests) == 2
        assert persisted.final_text == "Replacement direction completed"
    finally:
        if worker.is_alive():
            runtime.cancel(session.session_id)
        worker.join(3)
        runtime.close()
