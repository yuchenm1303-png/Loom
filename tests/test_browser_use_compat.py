from __future__ import annotations

import pytest

browser_use = pytest.importorskip("browser_use")

from app.agent_runtime.browser_backend import BrowserUseSessionBackend
from app.agent_runtime.browser_session import BrowserLaunchOptions
from app.agent_runtime.browser_use_backend import browser_use_available


def test_browser_use_013_adapter_import_and_event_contract():
    from browser_use import BrowserProfile, BrowserSession
    from browser_use.browser.events import CloseTabEvent, RefreshEvent, SwitchTabEvent

    assert browser_use_available() is True

    profile = BrowserProfile(
        headless=True,
        allowed_domains=("example.com",),
        prohibited_domains=("localhost", "*.localhost"),
        block_ip_addresses=True,
        user_data_dir=None,
        keep_alive=False,
    )
    session = BrowserSession(browser_profile=profile)
    assert session is not None
    assert RefreshEvent() is not None
    assert SwitchTabEvent(target_id="target-1").target_id == "target-1"
    assert CloseTabEvent(target_id="target-1").target_id == "target-1"

    backend = BrowserUseSessionBackend(
        BrowserLaunchOptions(headless=True, allowed_domains=("example.com",))
    )
    try:
        assert backend.backend_name == "browser-use"
        assert backend.state_revision == 0
    finally:
        backend.close()


def test_browser_use_backend_launches_real_headless_browser_and_captures_png():
    """Exercise the actual Chrome/CDP lifecycle without depending on external network."""

    backend = BrowserUseSessionBackend(
        BrowserLaunchOptions(headless=True),
        action_timeout_seconds=45.0,
    )
    try:
        state = backend.start()
        assert backend.state_revision >= 1
        assert state.url in {"", "about:blank"} or bool(state.tabs)

        refreshed = backend.refresh()
        assert backend.state_revision >= 2
        assert isinstance(refreshed.dom, str)

        png = backend.screenshot(full_page=False)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        backend.close()
