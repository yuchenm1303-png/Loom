from __future__ import annotations

from pathlib import Path

import pytest

import app.agent_runtime.browser_runtime as browser_runtime_module
import app.agent_runtime.browser_use_backend as browser_use_backend_module
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.browser_use_backend import BrowserUseBackend
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):  # pragma: no cover - these tests never sample a model
        raise AssertionError("CDP attach tests must not sample the model")


class FakeCDPBackend:
    backend_name = "browser-use"
    created: list["FakeCDPBackend"] = []

    def __init__(self, *, options: BrowserLaunchOptions) -> None:
        self.options = options
        self.user_data_dir = None
        self.cdp_url = None
        self.state_revision = 0
        self.closed = False
        type(self).created.append(self)

    def start(self) -> BrowserPageState:
        self.state_revision += 1
        return BrowserPageState(url="about:blank", title="Attached")

    def close(self) -> None:
        self.closed = True


def _runtime(tmp_path, monkeypatch, *, cdp_url: str | None = "http://127.0.0.1:9222"):
    FakeCDPBackend.created.clear()
    monkeypatch.setattr(browser_runtime_module, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_runtime_module, "BrowserUseSessionBackend", FakeCDPBackend)
    return browser_runtime_module.BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
        browser_cdp_url=cdp_url,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9222",
        "http://127.1.2.3:9222",
        "ws://127.0.0.1:9222/devtools/browser/abc",
        "http://[::1]:9222",
        "ws://[::1]:9222/devtools/browser/abc",
    ],
)
def test_local_cdp_url_accepts_only_literal_loopback_endpoints(url):
    assert browser_runtime_module._validate_local_cdp_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:9222",
        "http://192.168.1.10:9222",
        "http://8.8.8.8:9222",
        "http://127.0.0.1",
        "ftp://127.0.0.1:9222",
        "http://user:pass@127.0.0.1:9222",
        "http://127.0.0.1:9222?token=secret",
        "http://127.0.0.1:9222#fragment",
    ],
)
def test_local_cdp_url_rejects_hostnames_remote_hosts_and_secret_shaped_endpoints(url):
    with pytest.raises(ValueError, match="browser CDP URL"):
        browser_runtime_module._validate_local_cdp_url(url)


def test_runtime_attaches_to_configured_external_browser_without_exposing_endpoint(tmp_path, monkeypatch):
    endpoint = "http://127.0.0.1:9222"
    runtime = _runtime(tmp_path, monkeypatch, cdp_url=endpoint)
    try:
        assert runtime.browser_profile_dir is None
        status = runtime.browser_status()
        assert status["browser_connection"] == "cdp-attach"
        assert status["external_browser"] is True
        assert status["cdp_endpoint_exposed"] is False
        assert status["session_persistence"] == "external-browser"
        assert status["storage_state_persistence"] is True
        assert endpoint not in str(status)

        store = runtime.browser_sessions
        assert store is not None
        item = store.start("owner")
        backend = FakeCDPBackend.created[0]
        assert item.backend is backend
        assert backend.cdp_url == endpoint
        assert backend.user_data_dir is None
        assert store.max_sessions_total == 1

        tool = runtime.tools.get("browser_open")
        assert tool is not None
        assert "existing local chrome/edge" in tool.description.casefold()
        assert endpoint not in tool.description
    finally:
        runtime.close()


def test_runtime_reads_cdp_endpoint_from_process_configuration(tmp_path, monkeypatch):
    endpoint = "ws://127.0.0.1:9333/devtools/browser/runtime"
    monkeypatch.setenv("LOOM_BROWSER_CDP_URL", endpoint)
    runtime = _runtime(tmp_path, monkeypatch, cdp_url=None)
    try:
        assert runtime.browser_status()["browser_connection"] == "cdp-attach"
        item = runtime.browser_sessions.start("owner")
        assert item.backend.cdp_url == endpoint
    finally:
        runtime.close()


def test_cdp_attach_rejects_ambiguous_profile_configuration(tmp_path, monkeypatch):
    FakeCDPBackend.created.clear()
    monkeypatch.setattr(browser_runtime_module, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_runtime_module, "BrowserUseSessionBackend", FakeCDPBackend)
    with pytest.raises(ValueError, match="cannot be combined with browser_profile_dir"):
        browser_runtime_module.BrowserRuntime(
            platform=NoopPlatform(),
            store=FileAgentSessionStore(tmp_path / "state"),
            tools=loom_default_tools(),
            sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
            web_search_provider=None,
            auto_configure_web_search=False,
            browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
            browser_cdp_url="http://127.0.0.1:9222",
            browser_profile_dir=tmp_path / "other-profile",
        )


def test_cdp_backend_disconnects_without_killing_user_browser(monkeypatch):
    monkeypatch.setattr(browser_use_backend_module.importlib.util, "find_spec", lambda name: object())

    class ExternalSession:
        def __init__(self):
            self.stopped = 0
            self.killed = 0

        async def stop(self):
            self.stopped += 1

        async def kill(self):
            self.killed += 1

    backend = BrowserUseBackend(
        BrowserLaunchOptions(),
        cdp_url="http://127.0.0.1:9222",
    )
    session = ExternalSession()
    backend._session = session
    try:
        backend._runner.run(backend._close_async(), timeout=5.0)
        assert session.stopped == 1
        assert session.killed == 0
        assert backend._session is None
    finally:
        backend.close()
