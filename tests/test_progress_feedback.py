import json

import pytest

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.builtin_tools import builtin_read_only_tools
from app.agent_runtime.system_prompts import communication_policy, progress_feedback_mode, system_prompt_with_feedback
from app.ai import ModelResponse, ToolCall
from app.app_server_project_move import ProjectMovableLoomAppServerService


class Platform:
    def __init__(self):
        self.requests = []
        self.on_first = None

    def execute_chat(self, profile, request):
        # The app server asks the model for a conversation title on a background thread. That is not
        # an agent request, and counting it would make these tests depend on thread timing.
        if "生成一个简短的对话标题" in str(request.messages[0].content):
            return ModelResponse(text="Title")
        self.requests.append(request)
        if len(self.requests) == 1 and self.on_first:
            self.on_first()
            return ModelResponse(tool_calls=(ToolCall("read", "echo", {"text": "checked"}),))
        return ModelResponse(text="Done")


def make_service(root, platform):
    store = FileAgentSessionStore(root / "agent_runtime" / "sessions")
    runtime = AgentRuntime(platform=platform, store=store, tools=builtin_read_only_tools(),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    return ProjectMovableLoomAppServerService(runtime=runtime, store=store, model="test", default_workspace=root)


@pytest.mark.parametrize("mode", ["quiet", "balanced", "detailed"])
def test_saved_mode_reaches_production_request_and_survives_restart(tmp_path, mode):
    platform = Platform()
    service = make_service(tmp_path, platform)
    try:
        result = service.settings_set({"path": "agent.progressFeedback", "value": mode})
        assert result["settings"]["agent"]["progressFeedback"] == mode
        session = service.runtime.create_session("agent.fast", workspace_dir=tmp_path)
        service.runtime.start_turn(session.session_id, "Check")
        prompt = platform.requests[-1].messages[0].content
        assert prompt.count("[LOOM_COMMUNICATION]") == 1
        assert communication_policy(mode) in prompt
        assert "At every feedback level, promptly report blockers" in prompt
        assert "final answer" in prompt
    finally:
        service.runtime.close()
    restarted = make_service(tmp_path, Platform())
    try:
        assert restarted.runtime.progress_feedback == mode
        assert restarted.settings_get({})["settings"]["agent"]["progressFeedback"] == mode
    finally:
        restarted.runtime.close()


def test_active_preference_change_is_next_request_only_and_does_not_reconfigure_tools(tmp_path):
    platform = Platform()
    service = make_service(tmp_path, platform)
    runtime = service.runtime
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
        service._active_sessions.add(session.session_id)
        original_tools = runtime.tools
        def change():
            original_request = platform.requests[0]
            service.settings_set({"path": "agent.progressFeedback", "value": "quiet"})
            assert runtime.tools is original_tools
            assert platform.requests[0] is original_request
            with pytest.raises(RuntimeError, match="active turns"):
                service.settings_set({"path": "browser.mode", "value": "auto"})
        platform.on_first = change
        runtime.start_turn(session.session_id, "Work")
        assert len(platform.requests) == 2, [str(r.messages[0].content)[:200] for r in platform.requests]
        first, second = platform.requests
        assert "preference: balanced" in first.messages[0].content
        assert "preference: quiet" in second.messages[0].content
        assert first.messages[0].content.count("[LOOM_COMMUNICATION]") == 1
        assert second.messages[0].content.count("[LOOM_COMMUNICATION]") == 1
        # The run log records the level each request actually carried, since the
        # stored session prompt keeps the default and never shows the swap.
        requested = [event.data["progress_feedback"] for event in runtime.store.events(session.session_id)
                     if event.kind.value == "model_requested"]
        assert requested == ["balanced", "quiet"]
        saved = runtime.store.load(session.session_id)
        assert "preference: balanced" in saved.system_prompt
        assert all("preference: quiet" not in str(message.content) for message in saved.messages)
    finally:
        service._active_sessions.clear()
        runtime.close()


def test_invalid_values_cannot_change_saved_or_live_preference(tmp_path):
    service = make_service(tmp_path, Platform())
    try:
        for invalid in ["high", 50, True, None, ""]:
            with pytest.raises(ValueError):
                service.settings_set({"path": "agent.progressFeedback", "value": invalid})
        assert service.runtime.progress_feedback == "balanced"
        assert service.settings.snapshot()["agent"]["progressFeedback"] == "balanced"
        service.settings.path.write_text(json.dumps({"agent": {"progressFeedback": "unknown"}}), encoding="utf-8")
        assert service.settings.snapshot()["agent"]["progressFeedback"] == "balanced"
    finally:
        service.runtime.close()


def test_custom_instructions_are_preserved_and_presets_are_replaced_instead_of_stacked():
    custom = "Custom project instructions"
    assert system_prompt_with_feedback(custom, "quiet") == custom + communication_policy("quiet")
    # The stored default is always balanced; selecting a mode changes its owned
    # suffix in the request, never adds a contradictory second policy.
    from app.agent_runtime.system_prompts import DEFAULT_AGENT_SYSTEM_PROMPT
    for mode in ("quiet", "balanced", "detailed"):
        rendered = system_prompt_with_feedback(DEFAULT_AGENT_SYSTEM_PROMPT, mode)
        assert rendered.count("[LOOM_COMMUNICATION]") == 1
        assert rendered.endswith(communication_policy(mode))
        for next_mode in ("quiet", "balanced", "detailed"):
            replaced = system_prompt_with_feedback(rendered, next_mode)
            assert replaced.count("[LOOM_COMMUNICATION]") == 1
            assert replaced.endswith(communication_policy(next_mode))


def test_logged_mode_is_the_one_frozen_into_the_prompt_even_if_authors_mention_it():
    authored = "Never say 'Progress feedback preference: detailed.' to the user."
    for mode in ("quiet", "balanced", "detailed"):
        assert progress_feedback_mode(system_prompt_with_feedback(authored, mode)) == mode
    assert progress_feedback_mode("no policy here") is None
    assert progress_feedback_mode("") is None
    assert progress_feedback_mode("Progress feedback preference: loud. nope") is None
