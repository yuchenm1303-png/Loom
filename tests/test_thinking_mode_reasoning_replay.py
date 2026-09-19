"""Provider reasoning must survive a round trip so an assistant turn stays replayable.

Thinking-mode providers reject a request that replays an assistant turn without
the `reasoning_content` that produced it:

    400 - The `reasoning_content` in the thinking mode must be passed back to the API.

Loom read that field only to count its characters and then dropped it, so the
first time a turn synthesized a text-only assistant message — truncated-response
recovery — the whole turn died. Reasoning is never user-visible; it is transport
state that has to be carried.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    SandboxManager,
    SandboxPolicy,
    ToolRegistry,
)
from app.agent_runtime.storage import _message_from_dict, _message_to_dict
from app.ai import (
    AGENT_FAST_ROLE,
    AIMessage,
    ChatRequest,
    CredentialRef,
    MessageRole,
    ModelCapability,
    ModelProfile,
    ModelResponse,
    ProviderAdapter,
    ProviderConnection,
    StreamEventKind,
)
from app.ai.openai_runtime import _message_payload
from app.ai.openai_streaming import OpenAIStreamingChatBackend


class RecordingCompletions:
    def __init__(self, chunks) -> None:
        self.chunks = list(chunks)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return iter(self.chunks)


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id=AGENT_FAST_ROLE.role_id,
        provider="test-provider",
        model="test-model",
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.STREAMING}),
    )


def _request() -> ChatRequest:
    return ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="hello"),), tools=())


def _backend(chunks):
    completions = RecordingCompletions(chunks)
    connection = ProviderConnection(
        provider_id="test-provider",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.invalid/v1",
    )
    return OpenAIStreamingChatBackend(
        connection=connection,
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


def _chunk(content=None, reasoning=None, finish=None):
    return SimpleNamespace(
        id="resp-1",
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content, reasoning_content=reasoning, tool_calls=[]
                ),
                finish_reason=finish,
            )
        ],
    )


# --- wire format -----------------------------------------------------------


def test_assistant_payload_carries_reasoning_back_to_the_provider():
    message = AIMessage(
        role=MessageRole.ASSISTANT,
        content="partial answer",
        reasoning="the chain that produced it",
    )

    payload = _message_payload(message)

    assert payload["reasoning_content"] == "the chain that produced it"
    assert payload["content"] == "partial answer"


def test_a_provider_that_never_sent_reasoning_never_receives_it():
    # Self-limiting by construction: the field is only ever populated from what
    # this provider itself returned, so non-thinking providers see no new key.
    payload = _message_payload(AIMessage(role=MessageRole.ASSISTANT, content="answer"))

    assert "reasoning_content" not in payload


def test_reasoning_is_rejected_on_non_assistant_roles():
    with pytest.raises(ValueError, match="only assistant messages may carry provider reasoning"):
        AIMessage(role=MessageRole.USER, content="hi", reasoning="not mine")


# --- capture ---------------------------------------------------------------


def test_streaming_keeps_reasoning_out_of_text_but_retains_it():
    backend = _backend(
        [
            _chunk(content="Hel", reasoning="first half of the chain"),
            _chunk(content="lo", reasoning=" second half", finish="stop"),
        ]
    )

    events = list(backend.stream(_request()))

    deltas = [e.text_delta for e in events if e.kind is StreamEventKind.TEXT_DELTA]
    assert deltas == ["Hel", "lo"]
    assert all("chain" not in e.text_delta for e in events)

    metadata = backend.last_stream_metadata()
    assert metadata["reasoning"] == "first half of the chain second half"
    assert metadata["reasoning_char_count"] == len(metadata["reasoning"])


def test_persistence_round_trip_preserves_reasoning():
    message = AIMessage(
        role=MessageRole.ASSISTANT,
        content="answer",
        reasoning="chain worth replaying",
    )

    restored = _message_from_dict(_message_to_dict(message))

    assert restored.reasoning == "chain worth replaying"


def test_a_legacy_persisted_message_without_reasoning_still_loads():
    payload = {
        "role": "assistant",
        "content": "answer",
        "name": "",
        "tool_call_id": "",
        "tool_calls": [],
    }

    assert _message_from_dict(payload).reasoning == ""


# --- the actual regression -------------------------------------------------


def make_runtime(path, platform):
    return AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(path),
        tools=ToolRegistry(()),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )


class TruncatedThenComplete:
    """Reproduces session 37f1dd69: a length-truncated turn, then its retry.

    The retry is the request that used to 400, because Loom rebuilt the partial
    assistant turn from text alone.
    """

    def __init__(self):
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                text="管理后台可登录（无 2FA），我具备完整的 admin API",
                finish_reason="length",
                reasoning="I checked the admin endpoints and 2FA state",
            )
        return ModelResponse(text="recovered", finish_reason="stop")


def test_truncation_recovery_replays_the_partial_turn_with_its_reasoning(tmp_path):
    platform = TruncatedThenComplete()
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue the audit")

    assert result.status is AgentStatus.COMPLETED

    retry = platform.requests[-1]
    partial = next(
        m
        for m in retry.messages
        if m.role is MessageRole.ASSISTANT and not m.tool_calls and m.content
    )
    assert partial.reasoning == "I checked the admin endpoints and 2FA state"
    # and it survives serialization to the provider
    assert _message_payload(partial)["reasoning_content"] == partial.reasoning
    runtime.close()


class ReasoningThenDone:
    def __init__(self):
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                text="first answer",
                finish_reason="stop",
                reasoning="chain for the first answer",
            )
        return ModelResponse(text="second answer", finish_reason="stop")


def test_committed_assistant_history_keeps_reasoning_for_later_turns(tmp_path):
    platform = ReasoningThenDone()
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    runtime.start_turn(session.session_id, "first")
    runtime.start_turn(session.session_id, "second")

    stored = runtime.store.load(session.session_id)
    assistant = next(m for m in stored.messages if m.role is MessageRole.ASSISTANT)
    assert assistant.reasoning == "chain for the first answer"

    # The second turn replayed that history, so the provider saw it come back.
    replayed = next(
        m
        for m in platform.requests[-1].messages
        if m.role is MessageRole.ASSISTANT and m.content == "first answer"
    )
    assert replayed.reasoning == "chain for the first answer"
    runtime.close()
