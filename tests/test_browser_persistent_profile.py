from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import app.agent_runtime.browser_runtime as browser_runtime_module
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.browser_use_backend import BrowserUseBackend
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools


class NoopPlatform:
    def execute_chat(self, profile_id, request):  # pragma: no cover - these tests never sample a model
        raise AssertionError("persistent profile tests must not sample the model")


class FakeAutoBrowserBackend:
    backend_name = "browser-use"
    created: list["FakeAutoBrowserBackend"] = []

    def __init__(self, *, options: BrowserLaunchOptions) -> None:
        self.options = options
        self.user_data_dir = None
        self.closed = False
        self.state_revision = 0
        type(self).created.append(self)

    def start(self) -> BrowserPageState:
        self.state_revision += 1
        return BrowserPageState(url="about:blank", title="Blank")

    def close(self) -> None:
        self.closed = True


def _runtime(tmp_path, monkeypatch, *, persist: bool = True):
    FakeAutoBrowserBackend.created.clear()
    monkeypatch.setattr(browser_runtime_module, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_runtime_module, "BrowserUseSessionBackend", FakeAutoBrowserBackend)
    return browser_runtime_module.BrowserRuntime(
        platform=NoopPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
        browser_persist_profile=persist,
    )


def test_browser_use_runtime_uses_private_persistent_default_profile(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch, persist=True)
    expected = (tmp_path / "state" / "browser" / "profiles" / "default").resolve()
    try:
        status = runtime.browser_status()
        assert status["backend"] == "browser-use"
        assert status["session_persistence"] == "profile-persistent"
        assert status["storage_state_persistence"] is True
        assert status["profile_name"] == "default"
        assert status["profile_path_exposed"] is False
        assert runtime.browser_profile_dir == expected
        assert expected.is_dir()
        if os.name != "nt":
            assert stat.S_IMODE(expected.stat().st_mode) == 0o700

        store = runtime.browser_sessions
        assert store is not None
        item = store.start("owner")
        assert item.backend is FakeAutoBrowserBackend.created[0]
        assert Path(FakeAutoBrowserBackend.created[0].user_data_dir).resolve() == expected

        # Chromium locks one user-data directory to one browser process. Loom
        # therefore makes the persistent profile single-session and expects tabs
        # to be reused inside that process instead of racing a second Chrome.
        with pytest.raises(BrowserError, match="session limit reached"):
            store.start("another-owner")

        open_tool = runtime.tools.get("browser_open")
        assert open_tool is not None
        assert "persistent default browser profile" in open_tool.description.casefold()
        assert "cookie" in open_tool.description.casefold()
    finally:
        runtime.close()


def test_browser_profile_persistence_can_be_disabled(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch, persist=False)
    try:
        status = runtime.browser_status()
        assert status["session_persistence"] == "ephemeral"
        assert status["storage_state_persistence"] is False
        assert runtime.browser_profile_dir is None
        store = runtime.browser_sessions
        assert store is not None
        item = store.start("owner")
        assert item.backend is FakeAutoBrowserBackend.created[0]
        assert FakeAutoBrowserBackend.created[0].user_data_dir is None
        assert store.max_sessions_total == 8
    finally:
        runtime.close()


def test_browser_use_backend_forwards_user_data_dir_without_starting_chromium(tmp_path):
    pytest.importorskip("browser_use")
    profile_dir = (tmp_path / "profile").resolve()
    backend = BrowserUseBackend(BrowserLaunchOptions(), user_data_dir=profile_dir)
    try:
        session = backend._runner.run(backend._ensure_session(), timeout=10.0)
        assert Path(session.browser_profile.user_data_dir).resolve() == profile_dir
    finally:
        # _ensure_session only constructs browser-use objects; no Chromium process
        # has been started. Drop the object before closing the loop so the test does
        # not ask browser-use to kill a browser that never launched.
        backend._session = None
        backend.close()
