"""browser_open choosing which local browser to drive.

Loom's connection used to be settled entirely outside the model: the runtime was
configured once and browser_open took whatever that was. With the desktop switch
on, the model picks per session, which makes two things load-bearing - the
loopback restriction on attach, and the fact that a session on an external
browser filters privileged tabs even when the runtime default does not.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.browser_runtime import (
    BrowserRuntime,
    _probe_local_cdp_endpoint,
    discover_local_cdp_endpoints,
)
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools


LOOPBACK_CDP = "http://127.0.0.1:9222"


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


def _context(runtime, tmp_path) -> ToolContext:
    return ToolContext(session_id="s-1", turn_id="t-1", workspace=tmp_path)


def _open_tool(runtime):
    tool = runtime.tools.get("browser_open")
    assert tool is not None
    return tool


def test_choosing_a_browser_is_refused_until_the_user_allows_it(runtime, tmp_path):
    assert runtime.browser_model_controlled_connection is False
    tool = _open_tool(runtime)

    with pytest.raises(ValueError, match="disabled"):
        tool.handler(_context(runtime, tmp_path), {"connect": "attach", "cdp_url": LOOPBACK_CDP})
    with pytest.raises(ValueError, match="disabled"):
        tool.handler(_context(runtime, tmp_path), {"cdp_url": LOOPBACK_CDP})


def test_default_connection_needs_no_permission_and_reports_the_runtime_mode(runtime):
    factory, external, label = runtime.browser_session_connection("")
    assert factory is runtime.browser_sessions.backend_factory
    assert external is False
    assert label == "local-launch"


def test_attach_is_restricted_to_loopback_even_when_the_model_chooses(runtime):
    runtime.browser_model_controlled_connection = True

    factory, external, label = runtime.browser_session_connection("attach", cdp_url=LOOPBACK_CDP)
    assert label == "cdp-attach"
    assert external is True
    backend = factory(BrowserLaunchOptions(headless=True))
    try:
        assert backend.cdp_url == LOOPBACK_CDP
        # An attached browser owns its profile; Loom must not hand it a second one.
        assert backend.user_data_dir is None
    finally:
        backend.close()


@pytest.mark.parametrize(
    ("cdp_url", "expected"),
    [
        ("", "requires a loopback CDP URL"),
        ("http://10.0.0.5:9222", "loopback"),
        ("http://localhost:9222", "not a hostname"),
        ("http://user:pass@127.0.0.1:9222", "must not contain credentials"),
        ("http://127.0.0.1", "explicit port"),
        ("ftp://127.0.0.1:9222", "http/https/ws/wss"),
    ],
)
def test_a_model_cannot_point_loom_at_a_remote_or_malformed_endpoint(runtime, cdp_url, expected):
    runtime.browser_model_controlled_connection = True
    with pytest.raises(ValueError, match=expected):
        runtime.browser_session_connection("attach", cdp_url=cdp_url)


def test_unknown_connection_names_are_refused(runtime):
    runtime.browser_model_controlled_connection = True
    with pytest.raises(ValueError, match="must be one of"):
        runtime.browser_session_connection("remote-grid")


def test_launch_reuses_the_persistent_profile_preference(runtime):
    runtime.browser_model_controlled_connection = True

    factory, external, label = runtime.browser_session_connection("launch")
    assert (label, external) == ("local-launch", False)
    backend = factory(BrowserLaunchOptions(headless=True))
    try:
        assert backend.user_data_dir is not None
        assert backend.cdp_url is None
    finally:
        backend.close()

    runtime.browser_set_connection("local-launch", persist_profile=False)
    factory, _, _ = runtime.browser_session_connection("launch")
    backend = factory(BrowserLaunchOptions(headless=True))
    try:
        assert backend.user_data_dir is None
    finally:
        backend.close()


def test_an_external_session_hides_privileged_tabs_the_runtime_default_would_reject(runtime):
    """The filter has to follow the session, not the runtime-wide mode.

    A model attaching to a real browser while Loom's default is to launch its own
    still meets chrome:// and localhost tabs. With the store-level flag off those
    used to raise and fail the whole call instead of being hidden.
    """

    from app.agent_runtime.browser_session import BrowserPageState

    store = runtime.browser_sessions
    assert store.filter_unsafe_background_tabs is False

    state = BrowserPageState(
        url="https://example.com/",
        title="Example",
        tabs=(
            {"tab_id": "1", "url": "https://example.com/"},
            {"tab_id": "2", "url": "chrome://settings/"},
            {"tab_id": "3", "url": "http://127.0.0.1:7000/"},
        ),
    )

    own = BrowserLaunchOptions(headless=True)
    with pytest.raises(Exception):
        store._validated_state(state, own)

    external = BrowserLaunchOptions(headless=True, external_browser=True)
    checked = store._validated_state(state, external)
    assert [tab["url"] for tab in checked.tabs] == ["https://example.com/"]


def test_attachable_browsers_are_not_probed_until_the_user_allows_it(runtime):
    assert runtime.browser_attachable_browsers() == ()


def test_endpoint_discovery_ignores_ports_that_are_not_devtools(monkeypatch):
    seen: list[int] = []

    def fake_probe(port, timeout=0.0):
        seen.append(int(port))
        if int(port) == 9223:
            return {"cdp_url": f"http://127.0.0.1:{port}", "browser": "Edg/1.0", "protocol_version": "1.3"}
        return None

    monkeypatch.setattr("app.agent_runtime.browser_runtime._probe_local_cdp_endpoint", fake_probe)
    found = discover_local_cdp_endpoints((9222, 9223, 9224))

    assert seen == [9222, 9223, 9224]
    assert [item["cdp_url"] for item in found] == ["http://127.0.0.1:9223"]


def test_probe_returns_none_for_a_closed_port():
    # Port 1 is never a DevTools endpoint; this must fail fast rather than raise.
    assert _probe_local_cdp_endpoint(1, timeout=0.05) is None


def test_discovery_never_reports_a_control_channel_url(monkeypatch):
    payload = {
        "Browser": "Chrome/141.0",
        "Protocol-Version": "1.3",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/secret-token",
    }

    class _Response:
        status = 200

        def read(self, _size=None):
            import json

            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _Response())
    endpoint = _probe_local_cdp_endpoint(9222)

    assert endpoint is not None
    assert endpoint["browser"] == "Chrome/141.0"
    assert "secret-token" not in repr(endpoint)
