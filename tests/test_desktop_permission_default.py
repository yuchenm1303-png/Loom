from __future__ import annotations

import argparse

import loom_app_server
from app.agent_runtime import PermissionMode
from loom_cli import _resolve_new_permission_mode


def _args(mode: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(permission_mode=mode)


def test_cli_default_stays_approval(monkeypatch) -> None:
    monkeypatch.delenv("LOOM_PERMISSION_MODE", raising=False)
    assert _resolve_new_permission_mode(_args()) is PermissionMode.APPROVAL


def test_explicit_mode_and_env_win_over_the_default(monkeypatch) -> None:
    monkeypatch.delenv("LOOM_PERMISSION_MODE", raising=False)
    assert _resolve_new_permission_mode(_args("approval"), default=PermissionMode.FULL_ACCESS) is PermissionMode.APPROVAL
    monkeypatch.setenv("LOOM_PERMISSION_MODE", "workspace")
    assert _resolve_new_permission_mode(_args(), default=PermissionMode.FULL_ACCESS) is PermissionMode.WORKSPACE


def test_desktop_host_starts_new_conversations_with_full_access(monkeypatch, tmp_path) -> None:
    """The app server is what Electron starts: a new conversation must not stop
    at the first command of an unattended goal waiting for a click."""

    monkeypatch.delenv("LOOM_PERMISSION_MODE", raising=False)
    seen: dict[str, object] = {}

    def fake_build_runtime(args):
        class Runtime:
            reasoning = None
            reasoning_capability = None
        return Runtime(), object(), "model"

    def fake_serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(loom_app_server, "_build_runtime", fake_build_runtime)
    monkeypatch.setattr(loom_app_server, "_validate_reasoning_for_runtime", lambda **_: None)
    monkeypatch.setattr(loom_app_server, "serve_browser_policy_managed_streaming_stdio", fake_serve)
    monkeypatch.setattr(loom_app_server, "_configure_protocol_stdio", lambda: None, raising=False)
    assert loom_app_server.main(["--workspace", str(tmp_path)]) == 0
    assert seen["default_permission_mode"] is PermissionMode.FULL_ACCESS
