from __future__ import annotations

import json
from io import BytesIO

import pytest

from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserPageState
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext, validate_tool_arguments
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ImagePart, ModelResponse, ToolCall


class CaptureBackend:
    backend_name = "capture-fixture"

    def __init__(self, options):
        self.state_revision = 0
        self.clicked = 0
        self.captures = 0

    def state(self):
        self.state_revision += 1
        return BrowserPageState(url="https://example.test", title="Capture", dom="[1]<button>Toggle</button>")

    start = state

    def click(self, index):
        assert index == 1
        self.clicked += 1
        return self.state()

    def screenshot(self, *, full_page=False):
        from PIL import Image
        self.captures += 1
        output = BytesIO()
        Image.new("RGB", (500, 300), "blue" if self.clicked else "red").save(output, format="PNG")
        return output.getvalue()

    def close(self):
        pass


@pytest.fixture
def capture_runtime(tmp_path):
    runtime = BrowserRuntime(platform=object(), store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None, auto_configure_web_search=False, auto_configure_browser=False,
        browser_backend_factory=CaptureBackend, browser_security_policy=BrowserSecurityPolicy(resolve_dns=False))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session("test", workspace_dir=workspace, permission_mode="full-access")
    session.current_turn_id = "capture-turn"
    context = ToolContext(session.session_id, session.current_turn_id, workspace, permission_mode="full-access")
    opened = runtime.tools.get("browser_open").handler(context, {})
    yield runtime, session, context, opened
    runtime.close()


def test_capture_baseline_trigger_frames_manifest_and_transient_model_image(capture_runtime):
    from PIL import Image
    runtime, session, context, opened = capture_runtime
    args = {"browser_id": opened.data["browser_id"], "state_revision": opened.data["state_revision"],
            "frames": 3, "interval_ms": 40, "trigger": {"type": "click", "index": 1}}
    tool = runtime.tools.get("browser_capture_sequence")
    validate_tool_arguments(tool.input_schema, args)
    result = tool.handler(context, args)
    assert result.ok
    frames = result.data["capture"]["frames"]
    assert len(frames) == 3
    assert all(frame["capture_finished_ms"] >= frame["capture_started_ms"] for frame in frames)
    assert frames[1]["capture_started_ms"] >= frames[0]["capture_started_ms"] + 39
    assert result.data["capture"]["trigger_finished_ms"] >= result.data["capture"]["trigger_started_ms"]
    with Image.open(context.workspace / frames[0]["path"]) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(context.workspace / frames[1]["path"]) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)
    manifest = json.loads((context.workspace / result.data["manifest_path"]).read_text())
    assert manifest["kind"] == "sampled_screenshots"
    runtime._append_tool_result(session, ToolCall("capture", tool.name, args), result, failed=False)
    step = runtime._build_step_context(session, next_model_step=True)
    messages, metadata = runtime._prepare_model_request(session, step, None)
    assert metadata["workspace_visual_observation"]["image_count"] == 1
    assert any(isinstance(part, ImagePart) for m in messages if isinstance(m.content, tuple) for part in m.content)
    runtime.store.save(session)
    persisted = (runtime.store.session_dir(session.session_id) / "session.json").read_text(encoding="utf-8")
    assert "data:image" not in persisted
    assert "<button>" not in persisted


def test_stale_trigger_never_clicks_or_captures(capture_runtime):
    runtime, session, context, opened = capture_runtime
    browser_id = opened.data["browser_id"]
    runtime.browser_sessions.snapshot(session.session_id, browser_id, refresh=True)
    result = runtime.tools.get("browser_capture_sequence").handler(context, {
        "browser_id": browser_id, "state_revision": opened.data["state_revision"],
        "trigger": {"type": "click", "index": 1}, "frames": 2})
    assert not result.ok
    assert result.data["execution_status"] == "not_executed"
    backend = runtime.browser_sessions._owned(session.session_id, browser_id).backend
    assert backend.clicked == backend.captures == 0


@pytest.mark.parametrize("extra", [{"frames": 17}, {"interval_ms": 1},
    {"trigger": {"type": "click", "index": 1}},
    {"trigger": {"type": "eval", "expression": "x"}, "state_revision": 1}])
def test_capture_schema_rejects_unbounded_or_unbound_requests(capture_runtime, extra):
    runtime, _, _, opened = capture_runtime
    with pytest.raises(Exception):
        validate_tool_arguments(runtime.tools.get("browser_capture_sequence").input_schema,
                                {"browser_id": opened.data["browser_id"], **extra})


def test_capture_cancellation_does_not_inject_trigger(capture_runtime):
    runtime, _, context, opened = capture_runtime
    from dataclasses import replace
    context = replace(context, is_cancelled=lambda: True)
    with pytest.raises(RuntimeError, match="cancellation"):
        runtime.tools.get("browser_capture_sequence").handler(context, {"browser_id": opened.data["browser_id"]})


def test_cancellation_during_screenshot_never_injects_following_trigger(capture_runtime, monkeypatch):
    from dataclasses import replace
    runtime, session, context, opened = capture_runtime
    cancelled = False
    context = replace(context, is_cancelled=lambda: cancelled)
    backend = runtime.browser_sessions._owned(session.session_id, opened.data["browser_id"]).backend
    original = backend.screenshot
    def cancel_during_capture(**kwargs):
        nonlocal cancelled
        raw = original(**kwargs)
        cancelled = True
        return raw
    monkeypatch.setattr(backend, "screenshot", cancel_during_capture)
    with pytest.raises(RuntimeError, match="cancellation"):
        runtime.tools.get("browser_capture_sequence").handler(context, {
            "browser_id": opened.data["browser_id"], "state_revision": opened.data["state_revision"],
            "trigger": {"type": "click", "index": 1}, "frames": 2})
    assert backend.clicked == 0


def test_capture_is_sensitive_and_deferred(capture_runtime):
    from app.agent_runtime.contracts import ToolEffect
    from app.agent_runtime.tools import ToolExposure
    runtime, _, _, _ = capture_runtime
    tool = runtime.tools.get("browser_capture_sequence")
    assert tool.effect is ToolEffect.SENSITIVE
    assert tool.exposure is ToolExposure.DEFERRED


def test_partial_capture_exposes_evidence_without_replaying_trigger(capture_runtime, monkeypatch):
    runtime, session, context, opened = capture_runtime
    backend = runtime.browser_sessions._owned(session.session_id, opened.data["browser_id"]).backend
    original = backend.screenshot
    def flaky(**kwargs):
        if backend.captures:
            raise RuntimeError("capture backend failed")
        return original(**kwargs)
    monkeypatch.setattr(backend, "screenshot", flaky)
    result = runtime.tools.get("browser_capture_sequence").handler(context, {
        "browser_id": opened.data["browser_id"], "state_revision": opened.data["state_revision"],
        "trigger": {"type": "click", "index": 1}, "frames": 3, "interval_ms": 40})
    assert not result.ok
    assert result.data["capture"]["complete"] is False
    assert result.data["capture"]["trigger_attempted"] is True
    assert backend.clicked == 1
    assert len(result.data["capture"]["frames"]) == 1
    assert (context.workspace / result.data["manifest_path"]).is_file()
