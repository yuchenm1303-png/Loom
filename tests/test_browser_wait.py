"""browser_wait: waiting out an async page without re-reading the whole state.

Without a wait the only way to sit out a loading page was to request the state
in a loop, which costs a DOM serialization per attempt and puts a stream of
near-identical snapshots in front of the model.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_runtime.browser_backend import BrowserUseSessionBackend
from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("the model is never called in these tests")


@pytest.fixture
def runtime(tmp_path):
    built = BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    yield built
    built.close()


def _backend(evaluations):
    """A backend whose condition evaluation is scripted."""

    instance = object.__new__(BrowserUseSessionBackend)
    instance.options = BrowserLaunchOptions(headless=True)
    instance.diagnostics = None
    instance.action_timeout_seconds = 30.0
    calls: list[str] = []

    last: list[dict] = [{"ok": True, "value": False}]

    async def evaluate_async(expression, *, await_promise):
        calls.append(expression)
        # A real page keeps answering the same way once it has settled, so the
        # last scripted answer repeats rather than reverting to a bare false.
        if evaluations:
            last[0] = evaluations.pop(0)
        return last[0]

    class _Runner:
        @staticmethod
        def run(coroutine, *, timeout=None):
            return asyncio.run(coroutine)

    instance._evaluate_async = evaluate_async
    instance._runner = _Runner()
    instance._eval_calls = calls
    return instance


def test_a_condition_that_is_already_true_returns_at_once():
    backend = _backend([{"ok": True, "value": True}])

    outcome = backend.wait_for(until="document.readyState === 'complete'", timeout_seconds=5)

    assert outcome["satisfied"] is True
    assert len(backend._eval_calls) == 1


def test_a_condition_is_polled_until_it_holds():
    backend = _backend([
        {"ok": True, "value": False},
        {"ok": True, "value": False},
        {"ok": True, "value": True},
    ])

    outcome = backend.wait_for(until="window.ready", timeout_seconds=10)

    assert outcome["satisfied"] is True
    assert len(backend._eval_calls) == 3


def test_a_condition_that_never_holds_reports_unsatisfied_rather_than_raising():
    backend = _backend([])

    outcome = backend.wait_for(until="window.never", timeout_seconds=0.5)

    assert outcome["satisfied"] is False
    assert outcome["waited_ms"] >= 0


def test_a_throwing_condition_is_reported_but_still_polled():
    backend = _backend([
        {"ok": False, "error": "TypeError: undefined"},
        {"ok": True, "value": True},
    ])

    outcome = backend.wait_for(until="a.b.c", timeout_seconds=10)

    assert outcome["satisfied"] is True
    assert len(backend._eval_calls) == 2


def test_a_persistently_throwing_condition_surfaces_the_error():
    backend = _backend([{"ok": False, "error": "TypeError: nope"}])

    outcome = backend.wait_for(until="a.b.c", timeout_seconds=0.5)

    assert outcome["satisfied"] is False
    assert "TypeError" in str(outcome.get("error") or "")


def test_waiting_for_text_cannot_break_out_of_the_expression():
    """The needle is page-supplied text, not code."""

    import json

    payload = '"); window.pwned = 1; ("'
    backend = _backend([{"ok": True, "value": True}])

    backend.wait_for(for_text=payload, timeout_seconds=5)

    expression = backend._eval_calls[0]
    # The payload is embedded as an escaped JS string literal, so its raw form -
    # which would have closed indexOf( and started a new statement - is absent.
    assert payload not in expression
    assert json.dumps(payload) in expression
    assert expression.count("indexOf(") == 1


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"for_text": "a", "until": "true"}, "either for_text or until"),
        ({}, "needs seconds, for_text, or until"),
        ({"for_text": "x" * 501}, "exceeds 500 characters"),
        ({"until": "x" * 20001}, "exceeds 20,000 characters"),
    ],
)
def test_contradictory_or_oversized_waits_are_refused(kwargs, expected):
    backend = _backend([])
    with pytest.raises(ValueError, match=expected):
        backend.wait_for(**kwargs)


def test_the_tool_reports_an_unmet_condition_as_not_ok_but_still_returns_state(runtime, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    class _Backend:
        backend_name = "fake"
        state_revision = 1
        downloads_dir = None

        def start(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def state(self):
            return BrowserPageState(url="https://example.com/", title="Example")

        def wait_for(self, *, seconds=0.0, for_text="", until="", timeout_seconds=15.0):
            return {"satisfied": False, "waited_ms": 3000, "error": "TypeError: x"}

        def close(self):
            return None

    runtime.browser_sessions.backend_factory = lambda options: _Backend()
    context = ToolContext(session_id="s-1", turn_id="t-1", workspace=workspace)
    browser_id = runtime.tools.get("browser_open").handler(context, {}).data["browser_id"]

    result = runtime.tools.get("browser_wait").handler(
        context, {"browser_id": browser_id, "until": "window.x"}
    )

    # A wait that times out is a fact about the page, not a broken call, but it
    # must not read as success either.
    assert result.ok is False
    assert result.data["satisfied"] is False
    assert result.data["waited_ms"] == 3000
    assert result.data["url"] == "https://example.com/"
    assert "TypeError" in result.data["condition_error"]
