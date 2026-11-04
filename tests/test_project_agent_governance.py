"""Real runtime/graph boundaries, with scripted models and no provider requests."""
from contextlib import contextmanager
import threading
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.ai import ModelResponse, ModelUsage
from app.ai.execution_control import ModelAdmissionRefused
from app.agent_runtime import MultiAgentRuntime, FileAgentSessionStore, ToolRegistry
from app.project_runner import delegation, parallel
from app.project_runner.review import git
from app.project_runner.store import ProjectStore
from test_project_runner import build, control, create, cycle, reply, PLAN, Service
from test_project_parallel import PLAN as TEAM_PLAN
from test_project_budget import usage_event


def multi_project(tmp_path, monkeypatch):
    import test_app_server
    monkeypatch.setattr(test_app_server, "DurableAgentRuntime", MultiAgentRuntime)
    service, runtime, platform, workspace, source = build(tmp_path, [reply(PLAN)])
    root = control(service, create(service, source), "start")
    cycle(service)
    root = service.project_store.get(root["id"])
    return service, runtime, platform, root, root["activeEpisode"]["sessionId"], source


def legacy_child(runtime, parent):
    session = runtime.get_session(parent)
    child = runtime.create_session(session.profile_id, workspace_dir=session.workspace_dir,
                                   permission_mode=session.permission_mode)
    runtime.agent_graph.add_child(parent_session_id=parent, child_session_id=child.session_id)
    return child


@contextmanager
def pre_upgrade_execution(service):
    """Fixture for work already running when the old Host is upgraded."""
    activated = service.runtime._on_turn_activated
    owner = service.project_budget.session_owner
    service.runtime._on_turn_activated = lambda *_: None
    service.project_budget.session_owner = lambda _: None
    try:
        yield
    finally:
        service.runtime._on_turn_activated = activated
        service.project_budget.session_owner = owner


def test_project_cannot_spawn_or_wake_unbudgeted_branches_but_chat_still_can(tmp_path, monkeypatch):
    service, runtime, platform, root, parent, source = multi_project(tmp_path, monkeypatch)
    try:
        sessions = len(list(service.store.root.iterdir()))
        with pytest.raises(ValueError, match="persistent project scheduler"):
            runtime.agent_control.spawn(parent, "Unscheduled work")
        assert len(list(service.store.root.iterdir())) == sessions
        old = legacy_child(runtime, parent)
        with pytest.raises(ValueError, match="persistent project scheduler"):
            runtime.agent_control.send(parent, old.session_id, "Run again")
        assert runtime.durable_state.pending_count(old.session_id) == 0
        with pytest.raises(ModelAdmissionRefused, match="Unscheduled project"):
            service.project_budget.admit(SimpleNamespace(session_id=old.session_id))
        assert len(platform.requests) == 1
        platform.responses.append(ModelResponse(text="Normal chat delegation"))
        chat_child = runtime.agent_control.spawn(source, "Normal delegated task", history_mode="none", background=False)
        assert chat_child.session_status.value == "completed"
    finally:
        runtime.close()


def test_directly_resuming_an_old_child_is_stopped_before_sampling(tmp_path, monkeypatch):
    service, runtime, platform, root, parent, _ = multi_project(tmp_path, monkeypatch)
    try:
        child = legacy_child(runtime, parent)
        result = runtime.start_turn(child.session_id, "Old queued task")
        assert result.status.value != "completed"
        assert len(platform.requests) == 1
    finally:
        runtime.close()


def test_legacy_usage_is_imported_once_even_after_sqlite_reopen(tmp_path, monkeypatch):
    service, runtime, platform, root, parent, _ = multi_project(tmp_path, monkeypatch)
    try:
        child = legacy_child(runtime, parent)
        platform.responses.append(ModelResponse(text="Old result", usage=ModelUsage(total_tokens=987)))
        with pre_upgrade_execution(service):
            runtime.start_turn(child.session_id, "Past unaccounted work")
        assert service.project_budget.report(service.project_store.get(root["id"]))["chargedTokens"] == 5
        delegation.reconcile(service)
        assert service.project_budget.report(service.project_store.get(root["id"]))["chargedTokens"] == 992
        home = service.project_store.home
        service.project_store.close()
        service.project_store = ProjectStore(home)
        service.project_runner.store = service.project_store
        delegation.reconcile(service)
        assert service.project_budget.report(service.project_store.get(root["id"]))["chargedTokens"] == 992
        assert runtime.agent_graph.get(child.session_id).relation_status.value == "closed"
    finally:
        runtime.close()


def test_host_restart_quarantines_legacy_branch_and_imports_its_reported_usage(tmp_path, monkeypatch):
    from app.project_runner.runner import ProjectRunner
    from test_app_server import RecordingPlatform
    service, runtime, platform, root, parent, _ = multi_project(tmp_path, monkeypatch)
    fresh, closed = None, False
    try:
        child = legacy_child(runtime, parent)
        platform.responses.append(ModelResponse(text="Pre-upgrade result", usage=ModelUsage(total_tokens=987)))
        with pre_upgrade_execution(service):
            runtime.start_turn(child.session_id, "Past work")
        home = service.store.root.parent.parent
        workspace = runtime.get_session(parent).workspace_dir
        runtime.close()
        closed = True
        monkeypatch.setattr(ProjectRunner, "start", lambda _: None)
        store = FileAgentSessionStore(home)
        platform = RecordingPlatform([])
        fresh = MultiAgentRuntime(platform=platform, store=store, tools=ToolRegistry(()), auto_drain_queue=False)
        reopened = Service(runtime=fresh, store=store, model="test-model", default_workspace=workspace)
        assert fresh.agent_graph.get(child.session_id).relation_status.value == "closed"
        assert reopened.project_budget.report(reopened.project_store.get(root["id"]))["chargedTokens"] == 992
        with pytest.raises(ModelAdmissionRefused, match="Unscheduled project"):
            reopened.project_budget.admit(SimpleNamespace(session_id=child.session_id))
        assert not platform.requests
    finally:
        if fresh is not None:
            fresh.close()
        elif not closed:
            runtime.close()


def test_cancel_reaches_a_running_legacy_grandchild_and_charges_late_usage(tmp_path, monkeypatch):
    service, runtime, platform, root, parent, _ = multi_project(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    try:
        child = legacy_child(runtime, parent)
        grandchild = legacy_child(runtime, child.session_id)
        def slow(*_):
            entered.set()
            assert release.wait(10)
            return ModelResponse(text="Late result")
        platform.execute_chat = slow
        with pre_upgrade_execution(service):
            runtime.agent_control._submit(grandchild.session_id, lambda: runtime.start_turn(grandchild.session_id, "Old running work"))
            assert entered.wait(5)
        turn_id = runtime.get_session(grandchild.session_id).current_turn_id
        control(service, service.project_store.get(root["id"]), "cancel")
        release.set()
        result = runtime.agent_control.wait(parent, grandchild.session_id, timeout_seconds=5)
        assert result.session_status.value != "completed"
        assert runtime.agent_graph.get(child.session_id).relation_status.value == "closed"
        assert runtime.agent_graph.get(grandchild.session_id).relation_status.value == "closed"
        cycle(service)
        # A provider usage receipt can arrive after cancellation/episode settlement.
        late = usage_event(grandchild.session_id, turn_id, 987)
        service.project_budget.observe(late)
        service.project_budget.observe(late)
        service.project_budget.apply_late()
        assert service.project_budget.report(service.project_store.get(root["id"]))["chargedTokens"] == 992
    finally:
        release.set()
        runtime.close()


def pending_spawn(tmp_path, monkeypatch):
    service, runtime, _, _, source = build(tmp_path, [reply(TEAM_PLAN)])
    root = control(service, create(service, source, maxWorkers=2, reviewPolicy="independent"), "start")
    cycle(service)
    original = parallel.candidate
    monkeypatch.setattr(parallel, "candidate", lambda *_: (_ for _ in ()).throw(OSError("injected spawn failure")))
    cycle(service)
    monkeypatch.setattr(parallel, "candidate", original)
    return service, runtime, service.project_store.get(root["id"])


def test_prepared_spawn_survives_reopen_and_adopts_exact_worktree_once(tmp_path, monkeypatch):
    service, runtime, root = pending_spawn(tmp_path, monkeypatch)
    try:
        prepared = root["parallelSpawn"]["id"]
        assert root["control"] == "paused" and not root.get("teamChildren")
        home = service.project_store.home
        service.project_store.close()
        service.project_store = ProjectStore(home)
        service.project_runner.store = service.project_store
        control(service, service.project_store.get(root["id"]), "resume")
        service.project_runner.tick()
        latest = service.project_store.get(root["id"])
        assert latest["parallelSpawn"] is None
        assert latest["teamChildren"] == [{"runId": prepared, "taskId": "task-1", "state": "running"}]
        assert len(service.project_store.list()) == 2
        assert sorted(item.name for item in (home / "workspaces").iterdir()) == sorted([root["id"], prepared])
    finally:
        runtime.close()


def test_changed_preparation_is_preserved_and_retry_never_creates_another_child(tmp_path, monkeypatch):
    service, runtime, root = pending_spawn(tmp_path, monkeypatch)
    try:
        intent = root["parallelSpawn"]
        folder = service.project_store.home / "workspaces" / intent["id"]
        (folder / "keep.txt").write_text("User changes during recovery")
        for _ in range(2):
            control(service, service.project_store.get(root["id"]), "resume")
            service.project_runner.tick()
            current = service.project_store.get(root["id"])
            assert current["control"] == "paused" and current["parallelSpawn"]["id"] == intent["id"]
            assert not current.get("teamChildren") and len(service.project_store.list()) == 1
            assert (folder / "keep.txt").read_text() == "User changes during recovery"
    finally:
        runtime.close()


@pytest.mark.parametrize("modified", [False, True])
def test_cancelled_preparation_only_cleans_pristine_checkout(tmp_path, monkeypatch, modified):
    service, runtime, root = pending_spawn(tmp_path, monkeypatch)
    try:
        folder = service.project_store.home / "workspaces" / root["parallelSpawn"]["id"]
        if modified:
            (folder / "keep.txt").write_text("Keep my work")
        control(service, root, "cancel")
        service.project_runner.janitor.sweep(service.project_store, now=10**12)
        assert folder.exists() == modified
        assert bool(service.project_store.get(root["id"])["parallelSpawn"]) == modified
    finally:
        runtime.close()


@pytest.mark.parametrize("changed", ["clean", "untracked", "ignored", "commit"])
def test_upgrade_orphan_cleanup_retains_every_kind_of_user_change(tmp_path, changed):
    service, runtime, _, workspace, source = build(tmp_path, [])
    try:
        root = create(service, source)
        folder = service.project_store.home / "workspaces" / str(uuid.uuid4())
        folder.parent.mkdir(parents=True, exist_ok=True)
        git(workspace, "worktree", "add", "--detach", str(folder), root["baseCommit"])
        if changed == "untracked":
            (folder / "keep.txt").write_text("user work")
        elif changed == "ignored":
            exclude = Path(root["gitCommonDir"]) / "info" / "exclude"
            with exclude.open("a") as handle:
                handle.write("\nkeep.txt\n")
            (folder / "keep.txt").write_text("ignored user work")
            assert not git(folder, "status", "--porcelain")
        elif changed == "commit":
            (folder / "README.md").write_text("User commit")
            git(folder, "commit", "-am", "User work")
        service.project_runner.janitor.sweep(service.project_store, now=10**12)
        assert folder.exists() == (changed != "clean")
    finally:
        runtime.close()
