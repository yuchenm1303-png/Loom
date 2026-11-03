from types import SimpleNamespace

import pytest

from app.project_runner.history import read, status
from test_project_runner import build, create, control, cycle, reply, PLAN, Controller


@pytest.mark.parametrize("current,root,family,expected", [
    (None, None, [], "draft"),
    (None, {"state": "created"}, [], "ready"),
    ({"state": "running"}, {"state": "integrated", "workflowStatus": "completed"}, [], "running"),
    ({"state": "completed"}, {}, [], "completed"),
    (None, {"state": "integrated", "workflowStatus": "completed"}, [], "completed"),
    (None, {"state": "running"}, [{"control": "budget_limited"}], "needs_you"),
    (None, {}, [{"activeEpisode": {"state": "waiting_approval"}}], "needs_you"),
    (None, {"workflowControl": "paused", "control": "paused"}, [], "paused"),
    ({"state": "cancelled"}, {}, [], "cancelled"),
])
def test_history_describes_durable_work_not_chat_activity(current, root, family, expected):
    assert status(current, root, family) == expected


def test_real_history_excludes_internal_sessions_and_uses_objective(tmp_path):
    service, runtime, _, workspace, source = build(tmp_path, [reply(PLAN)])
    try:
        registered = service.projects.create(workspace, name="Ledger")
        run = control(service, create(service, source, objective="Build a ledger"), "start")
        cycle(service)
        agent = service.project_store.get(run["id"])["activeEpisode"]["sessionId"]
        result = Controller(service)._dispatch("projectWeave/history", {"threadIds": [source, agent]})
        assert [row["id"] for row in result["conversations"]] == [source]
        row = result["conversations"][0]
        assert row["title"] == "Build a ledger"
        assert row["status"] == "running"
        assert row["workspace"] and row["projectId"] == registered.project_id
        assert row["projectName"] == "Ledger"
        assert "messages" not in row and "usage" not in row
    finally:
        runtime.close()


def test_history_reads_beyond_sidebar_page_and_filters_archives_and_children(tmp_path):
    service, runtime, _, _, source = build(tmp_path, [])
    try:
        sessions = [SimpleNamespace(session_id=f"t{i}") for i in range(240)]
        service._list_session_objects = lambda **_: sessions
        service._managed_record = lambda item: {"id": item.session_id, "weave": True, "title": "Manual title",
            "titleSource": "manual", "workspace": "C:/work", "updatedAt": "2026-11-01T00:00:00Z",
            "archived": item.session_id == "t1", "parentThreadId": "parent" if item.session_id == "t2" else None}
        result = read(service, {})["conversations"]
        assert len(result) == 238
        assert "t239" in {row["id"] for row in result}
        assert not {"t1", "t2"} & {row["id"] for row in result}
        assert all(row["status"] == "draft" for row in result)
        with pytest.raises(ValueError):
            read(service, {"threadIds": [source] * 201})
    finally:
        runtime.close()


def test_production_metadata_preserves_manual_title_and_excludes_archived(tmp_path):
    from app.app_server_browser_policy import BrowserPolicyLoomAppServerService
    service, runtime, _, _, source = build(tmp_path, [], service_class=BrowserPolicyLoomAppServerService)
    try:
        create(service, source, objective="The full build objective")
        service.thread_rename({"threadId": source, "title": "My release"})
        rows = read(service, {})["conversations"]
        assert rows[0]["title"] == "My release"
        assert rows[0]["objective"] == "The full build objective"
        assert rows[0]["status"] == "ready"
        service.thread_archive({"threadId": source})
        assert read(service, {"threadIds": [source]})["conversations"] == []
    finally:
        runtime.close()
