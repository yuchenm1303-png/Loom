from __future__ import annotations

import json
from io import BytesIO

import pytest

from app.agent_runtime.browser_backend_registry import BrowserBackendRegistryMixin, EMBEDDED_BROWSER
from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserPageState
from app.agent_runtime.browser_session import BrowserURLPolicyError
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext, validate_tool_arguments
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ImagePart, ToolCall


class PageBackend:
    """A page whose pixels, element boxes and frame clock the test controls."""
    backend_name = "page-fixture"
    instances: list["PageBackend"] = []

    def __init__(self, options):
        self.state_revision = 0
        self.url = "about:blank"
        self.navigations = []
        self.clicked = 0
        self.size = (500, 300)
        self.patch = None  # (box, colour) painted over the page
        self.box = {"x": 100, "y": 50, "width": 100, "height": 50}
        self.frames = [i * 16.667 for i in range(60)]
        self.collect = True
        self.scripts = []
        PageBackend.instances.append(self)

    def state(self):
        self.state_revision += 1
        return BrowserPageState(url=self.url, title="Page", dom="[1]<button>Go</button>")

    start = state

    def navigate(self, url, *, new_tab=False):
        self.url = url
        self.navigations.append(url)
        return self.state()

    def click(self, index):
        self.clicked += 1
        return self.state()

    def screenshot(self, *, full_page=False):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", self.size, "white")
        if self.patch:
            ImageDraw.Draw(image).rectangle(self.patch[0], fill=self.patch[1])
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def evaluate(self, expression, *, await_promise=True):
        self.scripts.append(expression)
        if "fonts.ready" in expression:
            missing = "document.querySelector" in expression and '"#missing"' in expression
            return {"ok": True, "value": {"width": self.size[0], "height": self.size[1], "dpr": 1,
                                          "box": False if missing else self.box, "visibility": "visible"}}
        if "__loomMotion = m" in expression:
            return {"ok": True, "value": {"visibility": "visible"}}
        if "m.trigger = performance.now()" in expression:
            return {"ok": True, "value": True}
        if "getAnimations" in expression:
            return {"ok": True, "value": [{"kind": "CSSTransition", "target": "div.panel", "name": "transform",
                                           "duration_ms": 240, "delay_ms": 0, "easing": "ease-out",
                                           "state": "running"}]}
        if "m.stop(); window.__loomMotion = null" in expression:
            if not self.collect:
                return {"ok": True, "value": None}
            return {"ok": True, "value": {"start": 0, "trigger": 100.0, "end": self.frames[-1],
                                          "frames": self.frames, "longTasks": [[300, 80]], "shift": 0.02,
                                          "visibility": "visible"}}
        return {"ok": True, "value": None}

    def close(self):
        pass


@pytest.fixture
def page(tmp_path):
    PageBackend.instances.clear()
    runtime = BrowserRuntime(platform=object(), store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None, auto_configure_web_search=False, auto_configure_browser=False,
        browser_backend_factory=PageBackend,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False, allow_private_networks=True))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session("test", workspace_dir=workspace, permission_mode="full-access")
    session.current_turn_id = "look-turn"
    context = ToolContext(session.session_id, session.current_turn_id, workspace, permission_mode="full-access")
    opened = runtime.tools.get("browser_open").handler(context, {"url": "http://127.0.0.1:5199/app"})
    yield runtime, session, context, opened, PageBackend.instances[-1]
    runtime.close()


def look(runtime, context, opened, **extra):
    args = {"browser_id": opened.data["browser_id"], "settle_ms": 0, **extra}
    tool = runtime.tools.get("browser_look")
    validate_tool_arguments(tool.input_schema, args)
    return tool.handler(context, args)


def test_first_look_reloads_and_attaches_the_page(page):
    runtime, session, context, opened, backend = page
    before = len(backend.navigations)
    result = look(runtime, context, opened)
    assert result.ok and result.data["look"]["first_look"] is True
    assert backend.navigations[before:] == ["http://127.0.0.1:5199/app"]
    assert (context.workspace / result.data["look"]["path"]).exists()
    runtime._append_tool_result(session, ToolCall("look", "browser_look", {}), result, failed=False)
    messages, metadata = runtime._prepare_model_request(session, runtime._build_step_context(session, next_model_step=True), None)
    assert metadata["workspace_visual_observation"]["image_count"] == 1
    assert any(isinstance(part, ImagePart) for m in messages if isinstance(m.content, tuple) for part in m.content)


def test_a_page_that_painted_no_frames_is_flagged(page, monkeypatch):
    runtime, _, context, opened, backend = page
    original = backend.evaluate

    def offscreen(expression, **kwargs):
        outcome = original(expression, **kwargs)
        if "fonts.ready" in expression:
            outcome["value"]["painted"] = False
        return outcome
    monkeypatch.setattr(backend, "evaluate", offscreen)
    assert "painted no frames" in look(runtime, context, opened).content


def test_unchanged_page_says_the_edit_did_not_reach_it(page):
    runtime, _, context, opened, _ = page
    look(runtime, context, opened)
    result = look(runtime, context, opened)
    assert result.data["look"]["changed_box"] is None
    assert "did not reach the page" in result.content


def test_changed_region_is_measured_and_shown_side_by_side(page):
    from PIL import Image
    runtime, _, context, opened, backend = page
    look(runtime, context, opened)
    backend.patch = ((40, 60, 89, 99), "red")
    result = look(runtime, context, opened)
    data = result.data["look"]
    assert data["changed_box"] == [40, 60, 90, 100]
    assert data["changed_ratio"] == pytest.approx(50 * 40 / (500 * 300), rel=0.01)
    assert result.data["images"][0]["path"] == data["compare_path"]
    with Image.open(context.workspace / data["compare_path"]) as sheet:
        assert sheet.width > 2 * 500
    # The next look compares with this one, not with the first.
    assert look(runtime, context, opened).data["look"]["changed_box"] is None


def test_selector_crops_to_the_element_with_padding_and_keeps_its_own_baseline(page):
    from PIL import Image
    runtime, _, context, opened, _ = page
    look(runtime, context, opened)
    cropped = look(runtime, context, opened, selector=".panel")
    assert cropped.data["look"]["first_look"] is True  # a different view of the page
    with Image.open(context.workspace / cropped.data["look"]["path"]) as image:
        assert image.size == (132, 82)
    missing = look(runtime, context, opened, selector="#missing")
    assert "No element matches" in missing.content


def test_reload_false_keeps_the_page_and_a_resize_is_reported(page):
    runtime, _, context, opened, backend = page
    look(runtime, context, opened)
    before = len(backend.navigations)
    backend.size = (400, 300)
    result = look(runtime, context, opened, reload=False)
    assert len(backend.navigations) == before
    assert "Viewport size changed" in result.content


def measure(runtime, context, opened, backend, **extra):
    args = {"browser_id": opened.data["browser_id"], "duration_ms": 200, **extra}
    tool = runtime.tools.get("browser_measure_motion")
    validate_tool_arguments(tool.input_schema, args)
    return tool.handler(context, args)


def test_motion_reports_dropped_frames_and_the_longest_one_from_the_trigger(page):
    runtime, _, context, opened, backend = page
    frames = [i * 16.667 for i in range(30)]
    frames += [frames[-1] + 100 + i * 16.667 for i in range(30)]  # one 100 ms stall
    backend.frames = frames
    state = runtime.tools.get("browser_state").handler(context, {"browser_id": opened.data["browser_id"]})
    result = measure(runtime, context, opened, backend, state_revision=state.data["state_revision"],
                     trigger={"type": "click", "index": 1})
    motion = result.data["motion"]
    assert result.ok and backend.clicked == 1
    assert motion["refresh_hz"] == pytest.approx(60, abs=0.5)
    assert motion["dropped_frames"] == 5
    assert motion["longest_frame_ms"] == pytest.approx(100, abs=0.1)
    assert motion["longest_frame_at_ms"] == pytest.approx(frames[29] - 100.0, abs=0.1)
    assert motion["long_tasks"] == 1 and motion["layout_shift"] == 0.02
    assert motion["animations"][0]["name"] == "transform"
    assert any("m.trigger = performance.now()" in script for script in backend.scripts)


def test_smooth_motion_has_no_dropped_frames(page):
    runtime, _, context, opened, backend = page
    result = measure(runtime, context, opened, backend)
    assert result.ok and result.data["motion"]["dropped_frames"] == 0
    assert result.data["motion"]["triggered"] is False


def test_throttled_page_is_not_reported_as_a_measurement(page):
    runtime, _, context, opened, backend = page
    backend.frames = [i * 1000.0 for i in range(5)]
    result = measure(runtime, context, opened, backend)
    assert not result.ok and result.data["motion"]["valid"] is False
    assert "throttled" in result.content


def test_lost_recorder_is_an_error_not_a_result(page):
    runtime, _, context, opened, backend = page
    backend.collect = False
    with pytest.raises(RuntimeError, match="recorder was lost"):
        measure(runtime, context, opened, backend)


def test_look_counts_as_visual_evidence_and_motion_as_an_action():
    import inspect
    from app.agent_runtime import development_verification
    source = inspect.getsource(development_verification.DevelopmentVerification.observed)
    assert '"browser_look"' in source and '"browser_measure_motion"' in source


def _registry():
    return BrowserBackendRegistryMixin.__new__(BrowserBackendRegistryMixin)


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:5199/", "embedded"), ("http://localhost:3000/a", "embedded"),
    ("http://[::1]:8080/", "embedded"), ("http://app.localhost/", "embedded"),
    ("https://example.com/", ""), ("", ""),
])
def test_local_previews_open_in_loom_browser_inside_desktop(monkeypatch, url, expected):
    monkeypatch.setenv("LOOM_BROWSER_EMBEDDED_TOKEN", "t")
    assert _registry().browser_default_connection(url) == expected
    monkeypatch.delenv("LOOM_BROWSER_EMBEDDED_TOKEN")
    assert _registry().browser_default_connection(url) == ""


def test_embedded_is_selectable_only_inside_desktop(monkeypatch):
    registry = _registry()
    registry._browser_requested_backend = "current-browser"
    registry.browser_allow_external_backend_selection = False
    monkeypatch.delenv("LOOM_BROWSER_EMBEDDED_TOKEN", raising=False)
    assert registry._backend_model_selectable(EMBEDDED_BROWSER) is False
    monkeypatch.setenv("LOOM_BROWSER_EMBEDDED_TOKEN", "t")
    assert registry._backend_model_selectable(EMBEDDED_BROWSER) is True
    assert registry._backend_model_selectable("cdp") is False


def test_browser_open_without_connect_takes_the_default_for_its_url(page, monkeypatch):
    runtime, _, context, _, _ = page
    seen = []
    original = runtime.browser_session_connection
    monkeypatch.setattr(runtime, "browser_default_connection", lambda url: "launch" if "5173" in url else "",
                        raising=False)
    monkeypatch.setattr(runtime, "browser_session_connection",
                        lambda connect, **kw: (seen.append(connect), original("", **kw))[1])
    runtime.tools.get("browser_open").handler(context, {"url": "http://127.0.0.1:5173/"})
    monkeypatch.setattr(runtime, "browser_model_controlled_connection", True, raising=False)
    runtime.tools.get("browser_open").handler(context, {"url": "https://example.com/", "connect": "launch"})
    assert seen == ["launch", "launch"]


def test_blocked_local_address_says_how_to_allow_it():
    policy = BrowserSecurityPolicy(resolve_dns=False)
    for url in ("http://localhost:5173/", "http://127.0.0.1:5173/"):
        with pytest.raises(BrowserURLPolicyError, match="Allow private network addresses"):
            policy.validate(url)
    with pytest.raises(BrowserURLPolicyError) as blocked:
        BrowserSecurityPolicy(resolve_dns=False, allow_private_networks=True).validate("http://169.254.169.254/")
    assert "Allow private network" not in str(blocked.value)


class _Access:
    status = 200

    def __init__(self, allowed):
        self.allowed = allowed

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return json.dumps({"access": {"browserUse": self.allowed, "computerUse": False}}).encode()


@pytest.fixture
def gate(monkeypatch):
    from app.agent_runtime import account_tool_access as gate
    monkeypatch.setenv("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED", "1")
    monkeypatch.setenv("LOOM_ACCOUNT_API_BASE_URL", "https://example.test/v1")
    gate.set_account_tool_access_credential("grace-token")
    yield gate
    gate.set_account_tool_access_credential(None)


def _outage(*args, **kwargs):
    raise TimeoutError()


def test_an_outage_keeps_a_recent_confirmation(gate, monkeypatch):
    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: _Access(True))
    assert gate.account_capability_allowed("browserUse")
    monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)
    monkeypatch.setattr(gate, "urlopen", _outage)
    assert gate.account_capability_allowed("browserUse")
    status = gate.account_tool_access_status()
    assert status["status"] == "service_unavailable" and status["access_from"] == "recent_confirmation"
    assert status["refresh_in_seconds"] > 30  # not retried (and waited for) on every step


def test_an_outage_after_the_grace_window_fails_closed(gate, monkeypatch):
    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: _Access(True))
    assert gate.account_capability_allowed("browserUse")
    key, access, at = gate._LAST_CONFIRMED
    monkeypatch.setattr(gate, "_LAST_CONFIRMED", (key, access, at - gate._OUTAGE_GRACE_SECONDS - 1))
    monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)
    monkeypatch.setattr(gate, "urlopen", _outage)
    assert not gate.account_capability_allowed("browserUse")


@pytest.mark.parametrize("failure", ["401", "404"])
def test_an_answer_is_not_an_outage(gate, monkeypatch, failure):
    from urllib.error import HTTPError
    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: _Access(True))
    assert gate.account_capability_allowed("browserUse")
    monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)

    def refuse(*args, **kwargs):
        raise HTTPError("https://example.test", int(failure), "no", {}, None)
    monkeypatch.setattr(gate, "urlopen", refuse)
    assert not gate.account_capability_allowed("browserUse")


def test_an_outage_keeps_a_revocation_and_a_new_identity_gets_no_grace(gate, monkeypatch):
    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: _Access(False))
    assert not gate.account_capability_allowed("browserUse")
    monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)
    monkeypatch.setattr(gate, "urlopen", _outage)
    assert not gate.account_capability_allowed("browserUse")

    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: _Access(True))
    gate.set_account_tool_access_credential("first-account")
    assert gate.account_capability_allowed("browserUse")
    gate.set_account_tool_access_credential("second-account")
    monkeypatch.setattr(gate, "urlopen", _outage)
    assert not gate.account_capability_allowed("browserUse")
