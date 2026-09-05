from __future__ import annotations

import time

import pytest

browser_use = pytest.importorskip("browser_use")

from app.agent_runtime.browser_backend import BrowserUseSessionBackend
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserUnavailableError
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
    """Exercise a real Chrome/CDP lifecycle while tolerating browser-use's attach race.

    browser-use 0.13.x can occasionally launch Chrome and establish the root CDP
    connection before its SessionManager observes the first target within the
    upstream two-second attach window. Each retry uses a completely fresh backend
    and Chrome process; persistent launch/CDP failures still fail this test.
    """

    last_error: BrowserUnavailableError | None = None
    for attempt in range(3):
        backend = BrowserUseSessionBackend(
            BrowserLaunchOptions(headless=True),
            action_timeout_seconds=45.0,
        )
        try:
            try:
                state = backend.start()
            except BrowserUnavailableError as exc:
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(0.5)
                continue

            assert backend.state_revision >= 1
            assert state.url in {"", "about:blank"} or bool(state.tabs)

            refreshed = backend.refresh()
            assert backend.state_revision >= 2
            assert isinstance(refreshed.dom, str)

            png = backend.screenshot(full_page=False)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            return
        finally:
            backend.close()

    assert last_error is None, "browser smoke exhausted retries without a result"
