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


def _session(tmp_path, prompt: str, *, version: int = 0) -> AgentSession:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return AgentSession(
        session_id="session-1",
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

    loaded = runtime.get_session("session-1")

    assert loaded.system_prompt == DEFAULT_AGENT_SYSTEM_PROMPT
    assert loaded.system_prompt_version == DEFAULT_AGENT_SYSTEM_PROMPT_VERSION
    persisted = store.load("session-1")
    assert persisted.system_prompt == DEFAULT_AGENT_SYSTEM_PROMPT
    assert persisted.system_prompt_version == DEFAULT_AGENT_SYSTEM_PROMPT_VERSION


def test_custom_prompt_is_never_rewritten(tmp_path) -> None:
    store = FileAgentSessionStore(tmp_path / "state")
    custom = "CUSTOM PROJECT AGENT PROMPT"
    store.create(_session(tmp_path, custom))
    runtime = AgentRuntime(platform=_Platform(), store=store)

    loaded = runtime.get_session("session-1")

    assert loaded.system_prompt == custom
    assert loaded.system_prompt_version == 0
