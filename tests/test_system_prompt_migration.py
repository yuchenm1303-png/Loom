from __future__ import annotations

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


def test_legacy_default_prompt_is_upgraded_on_session_load(tmp_path) -> None:
    store = FileAgentSessionStore(tmp_path / "state")
    legacy = next(iter(_LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS))
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



def test_default_prompt_exposes_decision_cards_without_turning_routine_work_into_questions() -> None:
    assert DEFAULT_AGENT_SYSTEM_PROMPT_VERSION == 4
    assert "```loom-decision" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert '"title"' not in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "routine implementation details" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "If the user's intent is already clear, act instead" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "Put the fenced ```loom-decision block first" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "requires title and options" in DEFAULT_AGENT_SYSTEM_PROMPT
    # Version 3, version 2, and the pre-action-first default remain migratable.
    assert len(_LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS) >= 3
