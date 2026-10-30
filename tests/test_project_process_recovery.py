"""Kill a real Host process; recover only from its on-disk stores.

Scripted model responses keep these crash tests deterministic and unbilled.
Unlike graceful-reopen tests, no shutdown hook or SQLite close runs in the child.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from app.ai import ModelResponse, ModelUsage, ToolCall
from app.agent_runtime import AgentTool, DurableAgentRuntime, FileAgentSessionStore, ToolEffect, ToolRegistry
from app.project_runner.runner import ProjectRunner
from test_app_server import RecordingPlatform
from test_project_runner import PLAN, Service, build, control, create, cycle, reply


def _child(root, boundary):
    def checkpoint():
        (root / "ready").write_text(boundary, encoding="utf-8")
        threading.Event().wait()  # the parent forcibly terminates this process

    def side_effect(context, arguments):
        context.resolve_workspace_path("partial.txt").write_text("written once", encoding="utf-8")
        checkpoint()

    # A built-in workspace file tool can only touch the episode's own checkout; any
    # other tool may reach elsewhere.
    name = "write_workspace_text" if boundary == "workspace_tool_in_flight" else "persist_partial"
    tool = AgentTool(name=name, description="Persist partial work",
                     input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.MUTATING, handler=side_effect)
    service, _, platform, workspace, source = build(root, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="partial", name=tool.name, arguments={}),),
                      usage=ModelUsage(7, 0, 7))], tools=(tool,))
    run = control(service, create(service, source), "start")
    (root / "manifest.json").write_text(json.dumps({"runId": run["id"],
        "sessionHome": str(service.store.root.parent.parent), "workspace": str(workspace)}), encoding="utf-8")
    cycle(service)  # planner completes, but project settlement has not run
    if boundary == "completed":
        control(service, service.project_store.get(run["id"]), "pause")
        checkpoint()
    if boundary == "transaction":
        control(service, service.project_store.get(run["id"]), "pause")
        with service.project_store.transaction() as db:
            current = service.project_store._read(db, run["id"])
            current.update(objective="Uncommitted objective", tokensUsed=999)
            service.project_store.save(db, current, "uncommitted_change")
            db.execute("INSERT INTO project_commands VALUES(?,?,?)", ("uncommitted-command", "fingerprint", run["id"]))
            checkpoint()
    if boundary == "prepared":
        service.project_runner.executor.dispatch = lambda episode: checkpoint()
    elif boundary == "model_in_flight":
        platform.execute_chat = lambda *args, **kwargs: checkpoint()
    # Settles planner, records the worker intent, then crashes at the chosen point.
    service.project_runner.tick()
    threading.Event().wait()


def _kill_at(root, boundary):
    root.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")]))
    with (root / "child.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root), boundary],
                                 env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while not (root / "ready").exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"Child failed to reach {boundary}: {(root / 'child.log').read_text(encoding='utf-8')}")
                time.sleep(0.02)
        finally:
            if child.poll() is None:
                child.kill()  # kill only the exact process launched by this test
            child.wait(timeout=10)
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _reopen(manifest):
    store = FileAgentSessionStore(Path(manifest["sessionHome"]))
    platform = RecordingPlatform([reply({"action": "candidate", "summary": "Finished after restart"})])
    runtime = DurableAgentRuntime(platform=platform, store=store, tools=ToolRegistry(()), auto_drain_queue=False)
    service = Service(runtime=runtime, store=store, model="test-model", default_workspace=Path(manifest["workspace"]))
    return service, runtime, platform


def test_killed_host_never_replays_a_tool_whose_outcome_is_uncertain(tmp_path, monkeypatch):
    # A tool was running when the Host died: its side effect may be partial. Never
    # replay it and never guess; the run waits for reconciliation.
    manifest = _kill_at(tmp_path / "tool_in_flight", "tool_in_flight")
    monkeypatch.setattr(ProjectRunner, "start", lambda self: None)
    service, runtime, platform = _reopen(manifest)
    try:
        for _ in range(3):
            service.project_runner.tick()
        run = service.project_store.get(manifest["runId"])
        assert run["state"] == "needs_review" and run["control"] == "paused"
        assert run["activeEpisode"]["state"] == "outcome_unknown"
        assert run["episodesUsed"] == 2 and not platform.requests
        with service.project_store.lock:
            assert service.project_store.db.execute(
                "SELECT COUNT(*) FROM project_episodes WHERE run_id=?", (run["id"],)).fetchone()[0] == 2
        position = service.project_budget.report(run)
        workspace = service.project_store.home / "workspaces" / run["id"]
        assert (workspace / "partial.txt").read_text(encoding="utf-8") == "written once"
        assert position["knownTokens"] == 12 and position["unknownRequests"] == 0
        with pytest.raises(ValueError, match="reconciliation"):
            control(service, run, "resume")
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", ["prepared", "model_in_flight"])
def test_killed_host_continues_an_episode_in_which_no_tool_had_run(tmp_path, monkeypatch, boundary):
    # A Host update, sleep or crash while the model is thinking is the common case on a
    # multi-day run. The turn's durable events prove no tool ran, so there is nothing to
    # replay or reconcile: the turn is closed as interrupted and a new episode continues.
    manifest = _kill_at(tmp_path / boundary, boundary)
    monkeypatch.setattr(ProjectRunner, "start", lambda self: None)
    service, runtime, platform = _reopen(manifest)
    try:
        for _ in range(4):
            cycle(service)
        run = service.project_store.get(manifest["runId"])
        assert run["state"] == "ready_for_review", run["reason"]
        assert run["episodesUsed"] == 3 and len(platform.requests) == 1  # one new episode, nothing replayed
        with service.project_store.lock:
            states = [row[0] for row in service.project_store.db.execute(
                "SELECT state FROM project_episodes WHERE run_id=? ORDER BY rowid", (run["id"],))]
        assert states == ["completed", "interrupted", "completed"]
        kinds = [item["kind"] for item in service.project_store.events(run["id"], limit=200)]
        assert kinds.count("interrupted_episode_continued") == 1 and "needs_review" not in kinds
        position = service.project_budget.report(run)
        assert position["knownTokens"] == 10
        assert position["unknownRequests"] == (1 if boundary == "model_in_flight" else 0)
        worker = runtime.get_session(run["sessions"]["worker-0"])
        assert worker.status.value == "completed"
    finally:
        runtime.close()


def test_killed_host_continues_when_the_tool_in_flight_was_confined_to_the_checkout(tmp_path, monkeypatch):
    # On a multi-day run the Host most often dies while a tool runs. A tool that can only
    # write the episode's own checkout leaves at worst unfinished work there, which the
    # independent review judges anyway: continue with a new episode, told what may have
    # partly run, and never replay the tool.
    manifest = _kill_at(tmp_path / "workspace_tool_in_flight", "workspace_tool_in_flight")
    monkeypatch.setattr(ProjectRunner, "start", lambda self: None)
    service, runtime, platform = _reopen(manifest)
    try:
        for _ in range(4):
            cycle(service)
        run = service.project_store.get(manifest["runId"])
        assert run["state"] == "ready_for_review", run["reason"]
        assert run["episodesUsed"] == 3 and len(platform.requests) == 1  # one new episode, the tool not replayed
        workspace = service.project_store.home / "workspaces" / run["id"]
        assert (workspace / "partial.txt").read_text(encoding="utf-8") == "written once"
        kinds = [item["kind"] for item in service.project_store.events(run["id"], limit=200)]
        assert kinds.count("interrupted_episode_continued") == 1 and "needs_review" not in kinds
        prompt = str(platform.requests[0].messages)
        assert "may have partly run: write_workspace_text" in prompt
        assert service.project_budget.report(run)["knownTokens"] == 17  # 5 planner + 7 interrupted + 5 new episode
    finally:
        runtime.close()


def test_killed_host_imports_completed_turn_once_then_continues(tmp_path, monkeypatch):
    manifest = _kill_at(tmp_path / "completed", "completed")
    monkeypatch.setattr(ProjectRunner, "start", lambda self: None)
    service, runtime, platform = _reopen(manifest)
    try:
        for _ in range(3):
            service.project_runner.tick()
        run = service.project_store.get(manifest["runId"])
        assert run["phase"] == "working" and run["activeEpisode"] is None
        assert run["tokensUsed"] == 5 and run["episodesUsed"] == 1 and not platform.requests
        control(service, run, "resume")
        cycle(service)
        cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review"
        assert final["tokensUsed"] == 10 and final["episodesUsed"] == 2
        assert len(platform.requests) == 1
    finally:
        runtime.close()


def test_killed_host_rolls_back_partial_control_transaction(tmp_path, monkeypatch):
    manifest = _kill_at(tmp_path / "transaction", "transaction")
    monkeypatch.setattr(ProjectRunner, "start", lambda self: None)
    service, runtime, platform = _reopen(manifest)
    try:
        run = service.project_store.get(manifest["runId"])
        assert run["objective"] == "Implement a working project" and run["tokensUsed"] == 0
        with service.project_store.lock:
            assert service.project_store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert service.project_store.db.execute(
                "SELECT COUNT(*) FROM project_commands WHERE operation='uncommitted-command'").fetchone()[0] == 0
        assert all(event["kind"] != "uncommitted_change" for event in service.project_store.events(run["id"]))
        service.project_runner.tick()
        recovered = service.project_store.get(run["id"])
        assert recovered["tokensUsed"] == 5 and recovered["phase"] == "working"
        assert recovered["control"] == "paused" and not platform.requests
    finally:
        runtime.close()


if __name__ == "__main__":
    _child(Path(sys.argv[1]), sys.argv[2])
