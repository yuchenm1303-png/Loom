from __future__ import annotations

import pytest

import loom_chatgpt_mcp as module
from app.app_server_local_ipc import LocalAppServerUnavailable


class MissingLocalBackend:
    closed = False

    def __init__(self, *_args, **_kwargs):
        type(self).closed = False

    def connect_and_initialize(self, **_kwargs):
        raise LocalAppServerUnavailable("missing")

    def close(self):
        type(self).closed = True


class RecordingChildBackend:
    command = None
    initialized = False

    def __init__(self, command, **_kwargs):
        type(self).command = list(command)
        type(self).initialized = False

    def subscribe_stderr(self, _listener):
        return None

    def start_and_initialize(self, **_kwargs):
        type(self).initialized = True
        return {"protocolVersion": 1}

    def close(self):
        return None


def test_default_mode_requires_running_canonical_app_server(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "LoomLocalAppServerClient", MissingLocalBackend)
    args = module.build_parser().parse_args(["--workspace", str(tmp_path)])

    with pytest.raises(SystemExit, match="Start Loom Desktop first"):
        module._connect_backend(args)

    assert MissingLocalBackend.closed is True


def test_explicit_standalone_mode_claims_local_ipc_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "LoomAppServerClient", RecordingChildBackend)
    args = module.build_parser().parse_args(
        ["--workspace", str(tmp_path), "--no-local-attach"]
    )

    backend = module._connect_backend(args)
    try:
        assert RecordingChildBackend.initialized is True
        assert RecordingChildBackend.command is not None
        assert "--local-ipc" in RecordingChildBackend.command
    finally:
        backend.close()
