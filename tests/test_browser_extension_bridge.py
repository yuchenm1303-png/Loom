from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge
from app.agent_runtime.browser_runtime import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.session_store import FileAgentSessionStore
from app.agent_runtime.tools import loom_default_tools
from app.sandbox import SandboxManager, SandboxPolicy


class NoopPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("model execution is not expected in this test")


class FakeExtensionBridge:
    created: list["FakeExtensionBridge"] = []

    def __init__(self, *args, **kwargs):
        self.stopped = False
        self.created.append(self)

    def start(self):
        return self

    def stop(self):
        self.stopped = True

    def status(self):
        return {
            "connected": True,
            "token_exposed": False,
            "browser": "Microsoft Edge",
            "active_tab": {"title": "Example", "url": "https://example.com"},
        }

    def command(self, action, args, *, timeout_seconds=None):
        if action == "state":
            return {
                "url": "https://example.com",
                "title": "Example",
                "dom": "",
                "tabs": [],
                "page_info": {},
                "errors": [],
            }
        return {}


@pytest.fixture
def bridge(tmp_path):
    item = BrowserExtensionBridge(
        host="127.0.0.1",
        port=0,
        token="test-token",
        credential_dir=tmp_path,
    )
    yield item
    bridge.stop()


def test_status_never_exposes_token(bridge):
    data = json.dumps(bridge.status())
    assert "test-token" not in data
    assert "token_exposed" in data


def test_bridge_redacts_sensitive_command_payloads(tmp_path):
    bridge = BrowserExtensionBridge(
        host="127.0.0.1",
        port=0,
        token="sk-test-123",
        credential_dir=tmp_path,
    )
    try:
        payload = bridge._redact_command_for_diagnostics(  # noqa: SLF001
            {
                "action": "type_text",
                "args": {
                    "text": "abc",
                    "token": "secret",
                    "other": "safe",
                },
            }
        )
    finally:
        bridge.stop()
    data = json.dumps(payload)
    assert "sk-test-123" not in data
    assert "abc" not in data
    assert "text_length" in data
    assert "[REDACTED]" in data


def test_runtime_can_select_current_tab_extension_backend(tmp_path, monkeypatch):
    from app.agent_runtime import browser_runtime as browser_runtime_module

    FakeExtensionBridge.created.clear()
    monkeypatch.setenv("LOOM_BROWSER_BACKEND", "extension")
    monkeypatch.setattr(browser_runtime_module, "BrowserExtensionBridge", FakeExtensionBridge)

    runtime = browser_runtime_module.BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    try:
        status = runtime.browser_status()
        assert status["backend"] == "browser-extension"
        assert status["browser_connection"] == "extension-bridge"
        assert status["external_browser"] is True
        assert status["storage_state_persistence"] is True
        assert status["extension_bridge"]["connected"] is True
        assert status["extension_bridge"]["token_exposed"] is False

        store = runtime.browser_sessions
        assert store is not None
        item = store.start("owner")
        assert item.last_state.title == "Example"
        assert store.max_sessions_total == 1
        assert store.filter_unsafe_background_tabs is True

        tool = runtime.tools.get("browser_open")
        assert tool is not None
        description = tool.description.casefold()
        assert "installed loom browser extension" in description
        assert "http/https url" in description
        assert "out-of-policy background tabs remain open" in description
    finally:
        runtime.close()
    assert FakeExtensionBridge.created[0].stopped is True
