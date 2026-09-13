from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agent_runtime.context_budget import ContextBudgetExceeded, prepare_context
from app.ai import AIMessage, MessageRole, ModelUsage


class NoModelExecutor:
    def __init__(self) -> None:
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        raise AssertionError("an irreducible single-message overflow must fail closed locally")


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
    model_retries: int = 2


class Platform:
    pass


class Router:
    def definitions(self):
        return ()


class Step:
    tool_router = Router()


class Session:
    def __init__(self, messages):
        self.session_id = "session-overflow"
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class Store:
    def events(self, _session_id):
        return ()


class Runtime:
    def __init__(self):
        self.limits = Limits()
        self.platform = Platform()
        self.model_executor = NoModelExecutor()
        self.instruction_loader = InstructionLoader()
        self.store = Store()

    def _context_envelope(self, _session, _step):
        return Envelope()

    def _request_context_messages(self, _session, _step, _envelope):
        return (AIMessage(role=MessageRole.SYSTEM, content="runtime state"),)


def test_single_giant_user_message_fails_closed_instead_of_silent_user_truncation(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    giant = "G" * 60_000
    session = Session([AIMessage(role=MessageRole.USER, content=giant)])
    runtime = Runtime()

    with pytest.raises(ContextBudgetExceeded) as caught:
        prepare_context(runtime, session, Step(), object())

    assert "compaction request cannot fit" in str(caught.value)
    assert session.messages[0].content == giant
    assert runtime.model_executor.requests == []
