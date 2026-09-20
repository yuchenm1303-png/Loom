from __future__ import annotations

import base64
import json
import threading
import time
from urllib.request import Request, urlopen

import pytest

import app.agent_runtime.browser_runtime as browser_runtime_module
from app.agent_runtime.browser_diagnostics import BrowserDiagnosticLog, summarize_bridge_args, summarize_browser_state_payload
from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge, BrowserExtensionSessionBackend
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserError, BrowserLaunchOptions
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

    @property
    def closed(self):
        return self.stopped

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

    def call(self, action, args=None, *, timeout=None):
        self.calls.append((str(action), dict(args or {})))
        if action == "release_tabs":
            return {"released": 0}
        if action == "screenshot":
            return {"png_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")}
        return {
            "url": "https://example.com/",
            "title": "Example",
            "dom": "[0] <button> text=\"Continue\"",
            "tabs": [
                {
                    "tab_id": "7",
                    "window_id": "11",
                    "url": "https://example.com/",
                    "title": "Example",
                    "active": True,
                    "current_window": True,
                }
            ],
            "page_info": {"element_count": 1, "tab_id": "7"},
        }


def test_extension_backend_maps_browser_actions_to_bridge_commands():
    bridge = FakeExtensionBridge()
    backend = BrowserExtensionSessionBackend(options=BrowserLaunchOptions(), bridge=bridge)

    state = backend.start()
    assert state.url == "https://example.com/"
    assert state.tabs == (
        {
            "tab_id": "7",
            "window_id": "11",
            "url": "https://example.com/",
            "title": "Example",
            "active": True,
            "current_window": True,
        },
    )
    assert backend.state_revision == 1

    backend.click(0)
    backend.type_text(0, "hello", clear=False)
    backend.scroll("down", 500)
    data = backend.screenshot()
    assert data.startswith(b"\x89PNG")

    assert [action for action, _args in bridge.calls] == ["state", "click", "type_text", "scroll", "screenshot"]
    assert bridge.calls[1][1] == {"index": 0, "tab_id": "7"}
    assert bridge.calls[2][1] == {"index": 0, "text": "hello", "clear": False, "tab_id": "7"}


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
        assert bridge.diagnostics.status()["entries"] >= 3
    finally:
        bridge.stop()


def test_extension_bridge_timeout_removes_command_before_late_reconnect():
    bridge = BrowserExtensionBridge(port=0, token="test-token", command_timeout=1, poll_timeout=1)
    try:
        # Nothing ever polled this bridge, so the message is about the extension
        # not collecting commands. A command the extension did collect and then
        # failed to finish says something different on purpose - sending someone
        # to reinstall a working extension because a page was slow is how a real
        # 45-second browser_open got reported as a broken install.
        with pytest.raises(BrowserError, match="not collecting commands"):
            bridge.call("click", {"index": 4})

        status = bridge.status()
        assert status["pending_commands"] == 0
        assert status["queued_commands"] == 0

        # Simulate the extension reconnecting after Loom already reported the
        # timeout. The failed command must never be delivered later.
        with urlopen(
            Request(
                f"{bridge.url}/browser-extension/v1/poll?client_id=late-client&version=0.1",
                headers={"X-Loom-Token": "test-token"},
            ),
            timeout=3,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"ok": True, "command": None}
    finally:
        bridge.stop()


def test_a_command_nobody_collects_fails_long_before_the_command_timeout():
    """An absent extension used to cost the full 45 seconds, every call.

    Measured in real sessions: three browser_open calls spent 45-47s each to
    report a broken install. A live extension re-polls the moment each poll
    returns, so it stamps the bridge at least once per poll_timeout; silence
    past that window means the queue has no reader and waiting longer only
    delays the same answer.
    """

    bridge = BrowserExtensionBridge(port=0, token="test-token", command_timeout=30, poll_timeout=1)
    started = time.monotonic()
    try:
        with pytest.raises(BrowserError, match="not collecting commands"):
            bridge.call("state", {})
    finally:
        bridge.stop()
    assert time.monotonic() - started < 10, "an absent extension still waits out the command timeout"


def test_a_command_the_extension_collected_keeps_the_whole_window():
    """A slow page is not a broken install, and must not be reported as one.

    The old timeout said "install/enable the extension" whichever had happened,
    so a page that took too long sent the user to reinstall something that was
    working. The phase was already computed for the log; only the message did
    not use it.
    """

    bridge = BrowserExtensionBridge(port=0, token="test-token", command_timeout=3, poll_timeout=1)

    def collect() -> None:
        time.sleep(0.3)
        with bridge._condition:
            for command in bridge._commands:
                command.dispatched_at = time.monotonic()
            bridge._last_poll_at = time.monotonic()

    threading.Thread(target=collect, daemon=True).start()
    started = time.monotonic()
    try:
        with pytest.raises(BrowserError, match="collected this click command but did not finish"):
            bridge.call("click", {"index": 1})
    finally:
        bridge.stop()
    # It waited: the page was given its whole window rather than failed early.
    assert time.monotonic() - started >= 2.5


def test_extension_bridge_does_not_enable_page_cors():
    bridge = BrowserExtensionBridge(port=0, token="test-token")
    bridge.start()
    try:
        with urlopen(
            Request(
                f"{bridge.url}/browser-extension/v1/health",
                method="OPTIONS",
                headers={"Origin": "https://attacker.example"},
            ),
            timeout=2,
        ) as response:
            assert response.status == 200
            assert response.headers.get("Access-Control-Allow-Origin") is None
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


def test_extension_bridge_rejects_web_page_origin_even_with_token():
    bridge = BrowserExtensionBridge(port=0, token="test-token")
    bridge.start()
    try:
        with pytest.raises(Exception):
            urlopen(
                Request(
                    f"{bridge.url}/browser-extension/v1/poll?client_id=attacker",
                    headers={"X-Loom-Token": "test-token", "Origin": "https://attacker.example"},
                ),
                timeout=2,
            )
    finally:
        bridge.stop()


def test_generated_install_token_is_stable_and_not_the_old_development_token(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    first = BrowserExtensionBridge(port=0)
    second = BrowserExtensionBridge(port=0)
    assert first.token == second.token
    assert len(first.token) >= 32
    assert first.token != "loom-dev-browser-extension"
    assert first.token not in json.dumps(first.status())


def test_browser_diagnostics_redacts_typed_text(tmp_path):
    raw_args = {"index": 2, "text": "super secret password", "clear": True}
    safe_args = summarize_bridge_args("type_text", raw_args)
    assert safe_args == {
        "index": 2,
        "clear": True,
        "text_length": 21,
        "text_present": True,
    }

    state_summary = summarize_browser_state_payload(
        {"dom": 'Visible page text\n<input value="super secret password">'},
        include_dom_excerpt=False,
    )
    assert state_summary["dom_chars"] > 0
    assert state_summary["dom_excerpt"] == "[omitted after browser_type]"

    diagnostics = BrowserDiagnosticLog(root=tmp_path)
    diagnostics.event("browser_type", args=safe_args, api_key="sk-test-123", nested={"token": "abc"}, state=state_summary)
    data = diagnostics.path.read_text(encoding="utf-8")
    assert "super secret password" not in data
    assert "sk-test-123" not in data
    assert "abc" not in data
    assert "text_length" in data
    assert "[REDACTED]" in data


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
        # browser_open's description is where the model learns which browser it is
        # about to drive, so extension mode has to say it is the user's own.
        description = tool.description.casefold()
        assert "installed loom browser extension" in description
        assert "http/https url" in description
        assert "out-of-policy background tabs remain open" in description
    finally:
        runtime.close()
    assert FakeExtensionBridge.created[0].stopped is True


def test_reconfiguring_in_extension_mode_reuses_the_running_bridge(tmp_path, monkeypatch):
    """A second bridge would fight the first one for the loopback port.

    Dropping the reference does not stop the old server thread, and
    allow_reuse_address lets the replacement bind the same port anyway, so the
    extension's long poll reaches one bridge while Loom queues commands on the
    other. Toggling private networks reaches this path from Settings.
    """

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
        first = runtime.browser_extension_bridge
        assert len(FakeExtensionBridge.created) == 1

        assert runtime.browser_set_private_networks(True)["changed"] is True

        assert runtime.browser_extension_bridge is first
        assert len(FakeExtensionBridge.created) == 1, "a reconfigure built a second bridge"
        assert first.stopped is False
    finally:
        runtime.close()


def test_a_second_loom_instance_cannot_silently_take_the_bridge_port():
    """Windows SO_REUSEADDR lets a second socket listen on a live port.

    A checkout and the installed build both bound 39222, the extension long-polled
    into whichever one Windows routed it to, and the other queued commands nobody
    collected - reported as "the release build cannot connect to the browser",
    with the extension and its pairing both perfectly fine.
    """

    first = BrowserExtensionBridge(port=0, token="loom-port-conflict-test")
    first.start()
    assert first.port_conflict == ""
    try:
        second = BrowserExtensionBridge(port=first.port, token="loom-port-conflict-test")
        second.start()
        try:
            # Losing the port must not raise out of start(): the browser is one
            # capability and Loom still has to come up.
            assert second.port_conflict, "a second bridge silently bound a live port"
            assert str(first.port) in second.port_conflict
            assert "LOOM_BROWSER_EXTENSION_PORT" in second.port_conflict
            assert second.status()["port_conflict"] == second.port_conflict

            # And a command must say that, not time out into "extension unresponsive".
            with pytest.raises(BrowserError, match="already owns the browser bridge port"):
                second.call("state", {}, timeout=2)
        finally:
            second.stop()
    finally:
        first.stop()
