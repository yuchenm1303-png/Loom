"""Kill a real Host on both sides of child worktree creation, then resume its intent."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from app.project_runner import parallel
from app.project_runner.runner import ProjectRunner
from test_project_runner import build, control, create, cycle, reply
from test_project_parallel import PLAN
from test_project_process_recovery import _reopen


def child_main(folder, boundary):
    service, _, _, workspace, source = build(folder, [reply(PLAN)])
    root = control(service, create(service, source, maxWorkers=2, reviewPolicy="independent"), "start")
    cycle(service)
    (folder / "manifest.json").write_text(json.dumps({"runId": root["id"],
        "sessionHome": str(service.store.root.parent.parent), "workspace": str(workspace)}), encoding="utf-8")
    original = parallel.git
    def checkpoint():
        (folder / "ready").write_text(boundary, encoding="utf-8")
        threading.Event().wait()
    def interrupted(path, *args):
        if "worktree" in args and "add" in args:
            if boundary == "before":
                checkpoint()
            result = original(path, *args)
            checkpoint()
            return result
        return original(path, *args)
    parallel.git = interrupted
    service.project_runner.tick()


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_killed_spawn_reuses_its_recorded_child_and_never_replays_planning(tmp_path, monkeypatch, boundary):
    folder = tmp_path / boundary
    folder.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")]))
    with (folder / "child.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(folder), boundary], env=env,
                                 stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while not (folder / "ready").exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    pytest.fail((folder / "child.log").read_text(encoding="utf-8"))
                time.sleep(.02)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(ProjectRunner, "start", lambda self: None)
    service, runtime, platform = _reopen(manifest)
    try:
        root = service.project_store.get(manifest["runId"])
        prepared = root["parallelSpawn"]["id"]
        target = service.project_store.home / "workspaces" / prepared
        assert target.exists() == (boundary == "after")
        service.project_runner.tick()
        root = service.project_store.get(root["id"])
        assert root["parallelSpawn"] is None
        assert root["teamChildren"] == [{"runId": prepared, "taskId": "task-1", "state": "running"}]
        assert target.is_dir() and len(service.project_store.list()) == 2
        assert root["episodesUsed"] == 1 and not platform.requests
    finally:
        runtime.close()


if __name__ == "__main__":
    child_main(Path(sys.argv[1]), sys.argv[2])
