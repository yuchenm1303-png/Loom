"""Switching which browser Loom drives, without rebuilding the runtime stack.

Loom can reach a browser three ways - launch its own, attach to a local CDP
endpoint, or drive the user's current tab through the extension bridge - and
until now the choice was frozen at process start by environment variables. These
cover the runtime-level switch the desktop settings page drives.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("the model is never called in these tests")


LOOPBACK_CDP = "http://127.0.0.1:9222"


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


def test_default_runtime_launches_its_own_persistent_browser(runtime):
    assert runtime.browser_connection_mode == "local-launch"
    assert runtime.browser_profile_persistence is True
    assert runtime.browser_status()["external_browser"] is False


def test_switching_to_cdp_attach_and_back_reports_each_mode(runtime):
    status = runtime.browser_set_connection("cdp-attach", cdp_url=LOOPBACK_CDP)
    assert runtime.browser_connection_mode == "cdp-attach"
    assert status["browser_connection"] == "cdp-attach"
    assert status["external_browser"] is True
    # The endpoint stays inside the backend closure even after an explicit switch.
    assert status["cdp_endpoint_exposed"] is False
    assert LOOPBACK_CDP not in repr(status)

    status = runtime.browser_set_connection("local-launch")
    assert runtime.browser_connection_mode == "local-launch"
    assert status["browser_connection"] == "local-launch"
    assert status["external_browser"] is False


def test_mode_switch_rewrites_what_browser_open_tells_the_model(runtime):
    launched = runtime.tools.get("browser_open").description
    runtime.browser_set_connection("cdp-attach", cdp_url=LOOPBACK_CDP)
    attached = runtime.tools.get("browser_open").description

    assert attached != launched
    assert "existing local Chrome/Edge browser" in attached
    # Every other browser tool must survive the rebuild untouched.
    names = {tool.name for tool in runtime.tools.all()}
    assert {"browser_click", "browser_type", "browser_tabs", "browser_close"}.issubset(names)


def test_profile_persistence_preference_survives_a_trip_through_cdp_attach(runtime):
    """cdp-attach has no Loom profile, and that must not read back as a choice.

    browser_profile_persistence is derived per mode: an attached browser owns its
    own profile, so the flag is False there. Treating that as the user's standing
    preference silently downgraded local-launch to an ephemeral profile after any
    round trip.
    """

    assert runtime.browser_profile_persistence is True
    runtime.browser_set_connection("cdp-attach", cdp_url=LOOPBACK_CDP)
    assert runtime.browser_profile_persistence is False
    runtime.browser_set_connection("local-launch")
    assert runtime.browser_profile_persistence is True
    assert runtime.browser_profile_dir is not None


def test_explicitly_disabled_persistence_also_survives_the_round_trip(runtime):
    runtime.browser_set_connection("local-launch", persist_profile=False)
    assert runtime.browser_profile_persistence is False

    runtime.browser_set_connection("cdp-attach", cdp_url=LOOPBACK_CDP)
    runtime.browser_set_connection("local-launch")
    assert runtime.browser_profile_persistence is False
    assert runtime.browser_profile_dir is None


def test_preferred_engine_reaches_the_backend_as_a_browser_use_channel(runtime):
    runtime.browser_set_connection("local-launch", engine="chrome")
    backend = runtime.browser_sessions.backend_factory(BrowserLaunchOptions(headless=True))
    try:
        assert backend.browser_channel == "chrome"
    finally:
        backend.close()

    runtime.browser_set_connection("local-launch", engine="edge")
    backend = runtime.browser_sessions.backend_factory(BrowserLaunchOptions(headless=True))
    try:
        assert backend.browser_channel == "msedge"
    finally:
        backend.close()

    # "system" is not a browser-use channel; it has to fall through to the default.
    runtime.browser_set_connection("local-launch", engine="system")
    backend = runtime.browser_sessions.backend_factory(BrowserLaunchOptions(headless=True))
    try:
        assert backend.browser_channel == ""
    finally:
        backend.close()


@pytest.mark.parametrize(
    ("mode", "cdp_url", "expected"),
    [
        ("nope", "", "must be one of"),
        ("cdp-attach", "", "requires a loopback CDP URL"),
        ("cdp-attach", "http://10.0.0.5:9222", "loopback"),
        ("cdp-attach", "http://localhost:9222", "not a hostname"),
        ("cdp-attach", "http://user:pass@127.0.0.1:9222", "must not contain credentials"),
        ("cdp-attach", "http://127.0.0.1", "explicit port"),
    ],
)
def test_unsafe_or_unknown_connections_are_refused(runtime, mode, cdp_url, expected):
    before = runtime.browser_connection_mode
    with pytest.raises(ValueError, match=expected):
        runtime.browser_set_connection(mode, cdp_url=cdp_url)
    # A rejected switch must leave the working connection in place.
    assert runtime.browser_connection_mode == before
    assert runtime.browser_sessions is not None
