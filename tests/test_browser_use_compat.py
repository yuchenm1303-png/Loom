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
