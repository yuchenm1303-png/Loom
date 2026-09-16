from __future__ import annotations

import inspect

import pytest

from app.agent_runtime import AgentRuntime
from app.agent_runtime.contracts import AgentStatus
from app.agent_runtime.durable_runtime import DurableAgentRuntime


def _layers_above_durable() -> tuple[type, ...]:
    """Classes that must relay ``turn_id`` before it reaches the durable terminus.

    ``DurableAgentRuntime`` is where the id is finally consumed; the plain core
    runtime below it mints its own and is never reached in this composition.
    """
    mro = AgentRuntime.__mro__
    terminus = mro.index(DurableAgentRuntime)
    return tuple(cls for cls in mro[: terminus + 1] if "start_turn" in cls.__dict__)


def test_every_start_turn_layer_accepts_turn_id():
    for cls in _layers_above_durable():
        signature = inspect.signature(cls.__dict__["start_turn"])
        try:
            signature.bind(object(), "session", "hello", turn_id="TURN-123")
        except TypeError as exc:  # pragma: no cover - failure path is the message
            pytest.fail(f"{cls.__module__}.{cls.__name__}.start_turn drops turn_id: {exc}")


def test_turn_id_survives_the_full_runtime_chain(monkeypatch):
    """The app server mints the turn id, returns it to the client, then launches the
    turn on a background thread. A mixin that swallows the keyword leaves the thread
    with no recorded turn, so the client can never interrupt what it just started."""

    class _Result:
        status = AgentStatus.COMPLETED

    seen: list[str | None] = []

    def fake_start_turn(self, session_id, user_text, *, turn_id=None):
        seen.append(turn_id)
        return _Result()

    monkeypatch.setattr(DurableAgentRuntime, "start_turn", fake_start_turn)
    # Only the bookkeeping the intermediate layers touch before delegating.
    monkeypatch.setattr(AgentRuntime, "_clear_session_activations", lambda self, sid: None)
    monkeypatch.setattr(AgentRuntime, "_clear_browser_feedback", lambda self, sid: None)

    runtime = object.__new__(AgentRuntime)
    AgentRuntime.start_turn(runtime, "session", "hello", turn_id="TURN-123")

    assert seen == ["TURN-123"]
