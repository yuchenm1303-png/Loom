from __future__ import annotations

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, ToolRegistry
from app.agent_runtime.contracts import AgentSession, AgentStatus
from app.agent_runtime.storage import session_from_dict, session_to_dict
from app.ai import AIMessage, MessageRole, ModelResponse


def _provider_state():
    return (
        {
            "id": "rs_1",
            "type": "reasoning",
            "encrypted_content": "encrypted-reasoning-state",
            "summary": [],
        },
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "incomplete",
            "phase": "final_answer",
            "content": [
                {
                    "type": "output_text",
                    "text": "partial answer",
                    "annotations": [],
                }
            ],
        },
    )


def test_provider_output_state_survives_session_snapshot_round_trip(tmp_path) -> None:
    session = AgentSession(
        session_id="00000000-0000-0000-0000-000000000002",
        profile_id="agent.fast",
        system_prompt="system",
        workspace_dir=str(tmp_path),
        created_at="2026-09-12T00:00:00+00:00",
        updated_at="2026-09-12T00:00:00+00:00",
        messages=[
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="partial answer",
                provider_state=_provider_state(),
            )
        ],
    )

    payload = session_to_dict(session)
    restored = session_from_dict(payload)

    assert payload["messages"][0]["_provider_state"][0]["encrypted_content"] == "encrypted-reasoning-state"
    assert payload["messages"][0]["_provider_state"][1]["phase"] == "final_answer"
    assert "provider_state" not in payload["messages"][0]
    assert restored.messages[0].provider_state == _provider_state()


class _ScriptedPlatform:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.requests = []

    def execute_chat(self, _profile, request):
        self.requests.append(request)
        return next(self.responses)


def test_truncated_recovery_replays_responses_provider_state(tmp_path) -> None:
    state = _provider_state()
    platform = _ScriptedPlatform(
        [
            ModelResponse(
                text="partial answer",
                finish_reason="length",
                provider_state=state,
            ),
            ModelResponse(text="done", finish_reason="completed"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path),
        tools=ToolRegistry(),
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue")

    assert result.status is AgentStatus.COMPLETED
    retry_messages = platform.requests[1].messages
    replay = next(
        message
        for message in retry_messages
        if message.role is MessageRole.ASSISTANT and message.content == "partial answer"
    )
    assert replay.provider_state == state
    assert replay.reasoning_content == ""
    stored = runtime.store.load(session.session_id)
    assert not any(message.content == "partial answer" for message in stored.messages)
    runtime.close()


def test_compaction_sanitization_does_not_replay_raw_provider_state(tmp_path, monkeypatch) -> None:
    import app.agent_runtime.turn_runner as turn_runner

    state = _provider_state()
    platform = _ScriptedPlatform(
        [
            ModelResponse(
                text="private checkpoint echo",
                finish_reason="completed",
                provider_state=state,
            ),
            ModelResponse(text="safe answer", finish_reason="completed"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path),
        tools=ToolRegistry(),
    )
    session = runtime.create_session("agent.fast")
    original = turn_runner._strip_compaction_echo
    calls = 0

    def fake_strip(messages, text):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "", True
        return original(messages, text)

    monkeypatch.setattr(turn_runner, "_strip_compaction_echo", fake_strip)

    result = runtime.start_turn(session.session_id, "answer safely")

    assert result.status is AgentStatus.COMPLETED
    assert len(platform.requests) == 2
    assert not any(
        message.provider_state
        for message in platform.requests[1].messages
        if message.role is MessageRole.ASSISTANT
    )
    stored = runtime.store.load(session.session_id)
    assert all(message.provider_state != state for message in stored.messages)
    runtime.close()
