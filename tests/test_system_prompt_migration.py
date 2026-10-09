from __future__ import annotations

import pytest

from app.agent_runtime.contracts import AgentSession
from app.agent_runtime.runtime import (
    DEFAULT_AGENT_SYSTEM_PROMPT,
    DEFAULT_AGENT_SYSTEM_PROMPT_VERSION,
    _LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS,
    AgentRuntime,
)
from app.agent_runtime.storage import FileAgentSessionStore


class _Platform:
    pass


_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def _session(tmp_path, prompt: str, *, version: int = 0) -> AgentSession:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return AgentSession(
        session_id=_SESSION_ID,
        profile_id="agent.fast",
        system_prompt=prompt,
        system_prompt_version=version,
        workspace_dir=str(workspace),
        created_at="2026-09-22T00:00:00Z",
        updated_at="2026-09-22T00:00:00Z",
    )


@pytest.mark.parametrize("legacy", sorted(_LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS))
def test_legacy_default_prompt_is_upgraded_on_session_load(tmp_path, legacy) -> None:
    store = FileAgentSessionStore(tmp_path / "state")
    store.create(_session(tmp_path, legacy))
    runtime = AgentRuntime(platform=_Platform(), store=store)

    loaded = runtime.get_session(_SESSION_ID)

    assert loaded.system_prompt == DEFAULT_AGENT_SYSTEM_PROMPT
    assert loaded.system_prompt_version == DEFAULT_AGENT_SYSTEM_PROMPT_VERSION
    persisted = store.load(_SESSION_ID)
    assert persisted.system_prompt == DEFAULT_AGENT_SYSTEM_PROMPT
    assert persisted.system_prompt_version == DEFAULT_AGENT_SYSTEM_PROMPT_VERSION


def test_custom_prompt_is_never_rewritten(tmp_path) -> None:
    store = FileAgentSessionStore(tmp_path / "state")
    custom = "CUSTOM PROJECT AGENT PROMPT"
    store.create(_session(tmp_path, custom))
    runtime = AgentRuntime(platform=_Platform(), store=store)

    loaded = runtime.get_session(_SESSION_ID)

    assert loaded.system_prompt == custom
    assert loaded.system_prompt_version == 0


def test_app_server_patched_production_request_contains_turn_protocol(tmp_path):
    from app.agent_runtime import AgentRuntime as ProductionRuntime, SandboxManager, SandboxPolicy
    from app.app_server import LoomAppServerService
    from app.ai import ModelResponse
    from test_agent_request_layout import ScriptedPlatform
    store = FileAgentSessionStore(tmp_path / "state")
    platform = ScriptedPlatform([ModelResponse(text="Done")])
    runtime = ProductionRuntime(platform=platform, store=store,
                                sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    service = LoomAppServerService(runtime=runtime, store=store, model="test", default_workspace=tmp_path)
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Check")
        assert "A reply without a tool call ends your turn" in platform.requests[0].messages[0].content
        assert "one short sentence" in platform.requests[0].messages[0].content
        assert platform.requests[0].messages[0].content.count("[LOOM_COMMUNICATION]") == 1
    finally:
        runtime.close()



def test_default_prompt_exposes_decision_cards_without_turning_routine_work_into_questions() -> None:
    assert DEFAULT_AGENT_SYSTEM_PROMPT_VERSION == 12
    assert "```loom-decision" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert '"title"' not in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "routine implementation details" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "If the user's intent is already clear, act instead" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "Put the fenced ```loom-decision block first" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "requires title and options" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "Use parallel tool calls when several actions are independent" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "8-12" not in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "Routine tool receipts do not need an acknowledgment" in DEFAULT_AGENT_SYSTEM_PROMPT
    # Version 6 through the older decision/action defaults remain migratable.
    assert len(_LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS) >= 6


def test_production_runs_silent_batches_and_preserves_request_contract_after_reload(tmp_path):
    from app.agent_runtime import AgentRuntime as ProductionRuntime, SandboxManager, SandboxPolicy
    from app.agent_runtime.workspace_tools import loom_default_tools
    from app.agent_runtime.system_prompts import EXECUTION_COMMUNICATION_POLICY
    from app.ai import ModelResponse, ToolCall
    from test_agent_request_layout import ScriptedPlatform

    platform = ScriptedPlatform([
        ModelResponse(tool_calls=(ToolCall("a", "list_workspace_files", {}),
                                  ToolCall("b", "read_workspace_text", {"path": "input.txt"}))),
        ModelResponse(tool_calls=(ToolCall("c", "read_workspace_text", {"path": "input.txt"}),)),
        ModelResponse(text="Verified"), ModelResponse(text="Continued"),
    ])
    (tmp_path / "input.txt").write_text("input", encoding="utf-8")
    store = FileAgentSessionStore(tmp_path / "state")
    def create_runtime():
        return ProductionRuntime(platform=platform, store=store, tools=loom_default_tools(),
                                 sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    runtime = create_runtime()
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
        runtime.start_turn(session.session_id, "Read and verify")
        saved = store.load(session.session_id)
        assistants = [m for m in saved.messages if m.role.value == "assistant"]
        assert [m.content for m in assistants] == ["", "", "Verified"]
        assert [m.tool_call_id for m in saved.messages if m.role.value == "tool"] == ["a", "b", "c"]
        prefix = platform.requests[0].messages[0]
        runtime.close()
        runtime = create_runtime()
        runtime.start_turn(session.session_id, "Continue")
        for request in platform.requests:
            assert request.messages[0] == prefix
            assert request.messages[0].content.count(EXECUTION_COMMUNICATION_POLICY) == 1
            assert request.parallel_tool_calls is True
    finally:
        runtime.close()
