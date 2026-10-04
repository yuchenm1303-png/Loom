from types import SimpleNamespace

import pytest

from app.agent_runtime.browser_use_backend import BrowserUseBackend
from app.agent_runtime.browser_session import BrowserLaunchOptions


@pytest.mark.parametrize("channel,cdp,bundled", [
    ("", None, True), ("chrome", None, False), ("", "http://127.0.0.1:9222", False),
])
def test_bundled_browser_is_default_without_overriding_user_browser(monkeypatch, channel, cdp, bundled):
    import browser_use
    monkeypatch.setenv("LOOM_BUNDLED_BROWSER", "C:/Loom/browsers/chrome.exe")
    monkeypatch.setattr(browser_use, "BrowserProfile", lambda **kwargs: SimpleNamespace(**kwargs))

    async def close():
        pass

    monkeypatch.setattr(browser_use, "BrowserSession", lambda browser_profile:
                        SimpleNamespace(browser_profile=browser_profile, kill=close, stop=close))
    backend = BrowserUseBackend(BrowserLaunchOptions(headless=True), browser_channel=channel, cdp_url=cdp)
    try:
        session = backend._runner.run(backend._ensure_session(), timeout=5)
        assert hasattr(session.browser_profile, "executable_path") is bundled
        if bundled:
            assert session.browser_profile.executable_path == "C:/Loom/browsers/chrome.exe"
    finally:
        backend.close()
