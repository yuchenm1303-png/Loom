from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.agent_runtime.process_runtime import ProcessStore
from app.code_block_terminal import _dispatch


def _service(store, sessions):
    return SimpleNamespace(
        runtime=SimpleNamespace(process_store=store),
        _list_session_objects=lambda: sessions,
    )


def _session(session_id, workspace):
    return SimpleNamespace(session_id=session_id, workspace_dir=workspace)


def test_background_tasks_collect_all_threads_in_same_workspace_without_draining(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    store = ProcessStore()
    service = _service(store, [_session("one", workspace), _session("two", workspace), _session("elsewhere", other)])
    p1 = store.start(
        session_id="one", argv=(sys.executable, "-u", "-c", "print('alpha', flush=True)"),
        cwd=workspace, permission_mode="full-access", timeout_seconds=20,
    )
    p2 = store.start(
        session_id="two", argv=(sys.executable, "-u", "-c", "print('beta', flush=True)"),
        cwd=workspace, permission_mode="full-access", timeout_seconds=20,
    )
    hidden = store.start(
        session_id="elsewhere", argv=(sys.executable, "-u", "-c", "print('secret', flush=True)"),
        cwd=other, permission_mode="full-access", timeout_seconds=20,
    )
    try:
        for proc in (p1, p2, hidden):
            proc.wait()
        listed = _dispatch(service, "terminal/list", {"workspace": str(workspace)})["processes"]
        assert {item["processId"] for item in listed} == {p1.process_id, p2.process_id}
        assert all("stdout" not in item and "stderr" not in item for item in listed)
        assert all(isinstance(item["elapsedSeconds"], int) for item in listed)

        first = _dispatch(service, "terminal/detail", {
            "workspace": str(workspace), "processId": p1.process_id,
        })
        assert "alpha" in first["stdout"]
        # Reading the panel must not consume the streaming cursor used by exec_wait.
        assert "alpha" in p1.snapshot(drain_delta=True).stdout_delta
        assert _dispatch(service, "terminal/detail", {
            "workspace": str(workspace), "processId": p2.process_id,
        })["returnCode"] == 0

        with pytest.raises(ValueError):
            _dispatch(service, "terminal/detail", {
                "workspace": str(workspace), "processId": hidden.process_id,
            })
        with pytest.raises(ValueError):
            _dispatch(service, "terminal/interrupt", {
                "workspace": str(workspace), "processId": hidden.process_id,
            })
        assert _dispatch(service, "terminal/clear", {"workspace": str(workspace)})["removed"] == 2
        assert _dispatch(service, "terminal/list", {"workspace": str(workspace)})["processes"] == []
        assert store.get(hidden.process_id, session_id="elsewhere") is hidden
    finally:
        for proc in (p1, p2, hidden):
            proc.terminate_tree()


def test_background_tasks_can_terminate_agent_owned_process(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = ProcessStore()
    service = _service(store, [_session("agent-thread", workspace)])
    running = store.start(
        session_id="agent-thread",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(60)"),
        cwd=workspace, permission_mode="full-access", timeout_seconds=90,
    )
    try:
        assert running.snapshot().running
        assert _dispatch(service, "terminal/clear", {"workspace": str(workspace)})["removed"] == 0
        result = _dispatch(service, "terminal/terminate", {
            "workspace": str(workspace), "processId": running.process_id,
        })
        assert not result["running"]
        assert not running.snapshot().running
        assert len(_dispatch(service, "terminal/list", {"workspace": str(workspace)})["processes"]) == 1
    finally:
        running.terminate_tree()
