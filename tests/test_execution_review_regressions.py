from types import SimpleNamespace
import pytest

from app.agent_runtime.diff_tracker import TurnDiffTracker
from app.agent_runtime.tools import ToolContext


@pytest.fixture
def runtime(tmp_path):
    from app.agent_runtime.browser_runtime import BrowserRuntime
    from app.agent_runtime.browser_security import BrowserSecurityPolicy
    from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
    from app.agent_runtime.storage import FileAgentSessionStore
    platform = SimpleNamespace(execute_chat=lambda *_: pytest.fail("no real model requests"))
    built = BrowserRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
                           web_search_provider=None, auto_configure_web_search=False,
                           auto_configure_browser=True,
                           browser_security_policy=BrowserSecurityPolicy(resolve_dns=False))
    try:
        yield built
    finally:
        built.close()


def test_unchanged_diff_is_reused_and_invalidated_on_change(monkeypatch):
    import app.agent_runtime.diff_tracker as module
    tracker = TurnDiffTracker()
    original = module.difflib.unified_diff
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module.difflib, "unified_diff", counted)
    tracker.record_text_change("report.txt", before="old\n", after="new\n")
    first = tracker.snapshot()
    assert tracker.snapshot() == first
    assert len(calls) == 1
    short = tracker.snapshot(max_chars=5)
    assert short.truncated
    assert len(calls) == 2
    tracker.record_text_change("report.txt", before="new\n", after="latest\n")
    assert "latest" in tracker.snapshot().diff
    assert len(calls) == 3


def test_browser_network_reports_unsupported_capability_without_execution(runtime, tmp_path, monkeypatch):
    import app.agent_runtime.browser_tools as module
    backend = SimpleNamespace(backend_name="browser-extension")
    monkeypatch.setattr(module, "_store", lambda _: SimpleNamespace(_owned=lambda *_: SimpleNamespace(backend=backend)))
    result = runtime.tools.get("browser_network").handler(ToolContext("session", "turn", tmp_path),
                                                          {"browser_id": "browser", "action": "start"})
    assert not result.ok
    assert result.data["execution_status"] == "not_executed"
    assert result.data["capability"] == "network_capture"
    assert result.data["supported"] is False


def test_page_eval_preserves_typed_error_and_recovery(runtime, tmp_path, monkeypatch):
    import app.agent_runtime.browser_tools as module
    backend = SimpleNamespace(backend_name="browser-extension",
                              evaluate=lambda *_args, **_kwargs: {"ok": False, "error_name": "EvalError", "error": "Page policy blocks evaluation"})
    monkeypatch.setattr(module, "_store", lambda _: SimpleNamespace(_owned=lambda *_: SimpleNamespace(backend=backend)))
    result = runtime.tools.get("browser_eval").handler(ToolContext("session", "turn", tmp_path),
                                                       {"browser_id": "browser", "expression": "1"})
    assert not result.ok
    assert result.data["error_name"] == "EvalError"
    assert "browser_state" in result.data["recovery"]
