from __future__ import annotations

import base64
import json
import threading
import time
from urllib.request import Request, urlopen

import pytest

import app.agent_runtime.browser_runtime as browser_runtime_module
from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge, BrowserExtensionSessionBackend
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):  # pragma: no cover
        raise AssertionError("browser extension tests must not sample a model")


class FakeExtensionBridge:
    created: list["FakeExtensionBridge"] = []

    def __init__(self):
        self.started = False
        self.stopped = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        type(self).created.append(self)

    @classmethod
    def from_environment(cls):
        return cls()

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {
            "connected": True,
            "last_client_id": "fake-client",
            "last_client_version": "0.1.0",
            "pending_commands": 0,
            "queued_commands": 0,
        }

    def call(self, action, args=None):
        self.calls.append((str(action), dict(args or {})))
        if action == "screenshot":
            return {"png_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")}
        return {
            "url": "https://example.com/",
            "title": "Example",
            "dom": "[0] <button> text=\"Continue\"",
            "tabs": [{"tab_id": "7", "url": "https://example.com/", "title": "Example"}],
            "page_info": {"element_count": 1},
        }


def test_extension_backend_maps_browser_actions_to_bridge_commands():
    bridge = FakeExtensionBridge()
    backend = BrowserExtensionSessionBackend(options=BrowserLaunchOptions(), bridge=bridge)

    state = backend.start()
    assert state.url == "https://example.com/"
    assert backend.state_revision == 1

    backend.click(0)
    backend.type_text(0, "hello", clear=False)
    backend.scroll("down", 500)
    data = backend.screenshot()
    assert data.startswith(b"\x89PNG")

    assert [action for action, _args in bridge.calls] == ["state", "click", "type_text", "scroll", "screenshot"]
    assert bridge.calls[2][1] == {"index": 0, "text": "hello", "clear": False}


def test_extension_bridge_serves_long_poll_commands_and_results():
    bridge = BrowserExtensionBridge(port=0, token="test-token", command_timeout=5, poll_timeout=5)
    bridge.start()
    try:
        result_holder = {}

        def caller():
            result_holder["value"] = bridge.call("state", {"x": 1})

        thread = threading.Thread(target=caller)
        thread.start()

        command = None
        deadline = time.monotonic() + 5
        while command is None and time.monotonic() < deadline:
            with urlopen(
                Request(
                    f"{bridge.url}/browser-extension/v1/poll?client_id=test-client&version=0.1",
                    headers={"X-Loom-Token": "test-token"},
                ),
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            command = payload.get("command")
        assert command is not None
        assert command["action"] == "state"
        assert command["args"] == {"x": 1}

        body = json.dumps(
            {
                "id": command["id"],
                "ok": True,
                "result": {"url": "https://example.com/", "title": "Example"},
            }
        ).encode("utf-8")
        with urlopen(
            Request(
                f"{bridge.url}/browser-extension/v1/result",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "X-Loom-Token": "test-token"},
            ),
            timeout=5,
        ) as response:
            assert response.status == 200

        thread.join(timeout=5)
        assert result_holder["value"] == {"url": "https://example.com/", "title": "Example"}
        assert bridge.connected is True
    finally:
        bridge.stop()


def test_extension_bridge_rejects_missing_token():
    bridge = BrowserExtensionBridge(port=0, token="test-token")
    bridge.start()
    try:
        with pytest.raises(Exception):
            urlopen(f"{bridge.url}/browser-extension/v1/poll?client_id=test", timeout=2)
    finally:
        bridge.stop()


def test_runtime_can_select_current_tab_extension_backend(tmp_path, monkeypatch):
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
        assert "currently active chrome/edge tab" in tool.description.casefold()
    finally:
        runtime.close()
    assert FakeExtensionBridge.created[0].stopped is True
