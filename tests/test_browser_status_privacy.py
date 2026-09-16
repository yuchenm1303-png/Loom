from __future__ import annotations

import json

from app.agent_runtime.browser_status_privacy import BrowserStatusPrivacyMixin


class RawBrowserStatusBase:
    def browser_backend_registry(self):
        return (
            {
                "id": "current-browser",
                "current_tab": {
                    "title": "OAuth callback token=super-secret-value",
                    "url": "https://example.com/callback?access_token=secret-token&code=abc#id_token=jwt",
                    "tab_id": "17",
                    "window_id": "9",
                },
            },
            {"id": "isolated"},
        )

    def browser_status(self, owner_session_id=None):
        del owner_session_id
        return {
            "extension_bridge": {
                "connected": True,
                "current_tab": {
                    "title": "Account secret=hidden",
                    "url": "https://accounts.example.com/path?code=very-secret#fragment",
                    "tab_id": "2",
                    "window_id": "3",
                },
            },
            "browser_backends": list(self.browser_backend_registry()),
        }


class Harness(BrowserStatusPrivacyMixin, RawBrowserStatusBase):
    pass


def test_backend_registry_exposes_origin_not_address_bar_secrets():
    rows = Harness().browser_backend_registry()
    tab = rows[0]["current_tab"]
    assert tab["url"] == "https://example.com"
    serialized = json.dumps(rows)
    assert "secret-token" not in serialized
    assert "id_token" not in serialized
    assert "super-secret-value" not in serialized


def test_runtime_status_sanitizes_extension_and_registry_current_tabs():
    status = Harness().browser_status()
    extension_tab = status["extension_bridge"]["current_tab"]
    backend_tab = status["browser_backends"][0]["current_tab"]
    assert extension_tab["url"] == "https://accounts.example.com"
    assert backend_tab["url"] == "https://example.com"
    serialized = json.dumps(status)
    assert "very-secret" not in serialized
    assert "secret-token" not in serialized


def test_non_web_privileged_tab_url_is_not_exposed():
    base = RawBrowserStatusBase()
    base.browser_backend_registry = lambda: (
        {
            "id": "current-browser",
            "current_tab": {
                "title": "Extensions",
                "url": "edge://extensions/?token=secret",
                "tab_id": "1",
                "window_id": "1",
            },
        },
    )

    class DynamicHarness(BrowserStatusPrivacyMixin):
        def browser_backend_registry(self):
            return BrowserStatusPrivacyMixin.browser_backend_registry(self)

    # Use an ordinary two-class harness so super() reaches the dynamic base.
    Safe = type("Safe", (BrowserStatusPrivacyMixin, base.__class__), {})
    rows = Safe().browser_backend_registry()
    assert rows[0]["current_tab"]["url"] == "https://example.com"
