from __future__ import annotations

from dataclasses import dataclass

from app.agent_runtime.context_budget import prepare_context
from app.ai import AIMessage, MessageRole, ModelUsage


class NoModelExecutor:
    def __init__(self) -> None:
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        raise AssertionError("non-archivable emergency reduction must not invoke compaction")


class InstructionLoader:
    def load(self, _workspace):
        return ""


class Envelope:
    digest = "emergency-overflow"


@dataclass
class Limits:
    context_window_tokens: int = 4000
    output_reserve_tokens: int = 600
    max_messages: int = 160
    max_tool_result_chars: int = 20_000


class Platform:
    pass


class Router:
    def definitions(self):
        return ()


class Step:
    tool_router = Router()


class Session:
    def __init__(self, messages):
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class Runtime:
    def __init__(self):
        self.limits = Limits()
        self.platform = Platform()
        self.model_executor = NoModelExecutor()
        self.instruction_loader = InstructionLoader()

    def _context_envelope(self, _session, _step):
        return Envelope()

    def _request_context_messages(self, _session, _step, _envelope):
        return (AIMessage(role=MessageRole.SYSTEM, content="runtime state"),)


def test_single_giant_user_message_uses_request_visible_emergency_truncation(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    giant = "G" * 60_000
    session = Session([AIMessage(role=MessageRole.USER, content=giant)])
    runtime = Runtime()

    messages, metadata = prepare_context(runtime, session, Step(), object())

    visible_user = next(message for message in messages if message.role is MessageRole.USER)
    assert len(str(visible_user.content)) < len(giant)
    assert "omitted for context budget" in str(visible_user.content)
    assert session.messages[0].content == giant
    assert runtime.model_executor.requests == []
    assert metadata["emergency_user_truncation"] is True
    assert metadata["user_messages_truncated"] >= 1
    assert metadata["estimated_input_tokens_after"] < metadata["estimated_input_tokens_before"]
    hard_target = (
        metadata["context_limits"]["input_budget_tokens"]
        - metadata["context_limits"]["safety_tokens"]
    )
    assert metadata["estimated_input_tokens_after"] <= hard_target
