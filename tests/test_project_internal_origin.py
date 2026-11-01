"""Internal detached project checkouts are agents, not new project sources."""
import uuid

import pytest

from app.project_runner.service import git
from test_project_runner import PLAN, build, control, create, cycle, reply


def test_internal_agent_overview_resolves_its_project_and_cannot_nest_a_project(tmp_path):
    service, runtime, platform, workspace, source = build(tmp_path, [reply(PLAN)])
    try:
        run = control(service, create(service, source), "start")
        cycle(service)
        current = service.project_store.get(run["id"])
        agent = current["activeEpisode"]["sessionId"]
        view = service.project_overview({"threadId": agent})
        assert view["run"]["id"] == run["id"]
        assert view["run"]["sourceThreadId"] == source
        assert view["run"]["repository"] == str(workspace.resolve())
        create(service, source)  # a newer project must not steal the old agent's overview
        assert service.project_overview({"threadId": agent})["run"]["id"] == run["id"]
        with pytest.raises(ValueError, match="original conversation"):
            service.project_run_create({"threadId": agent})
        assert len(service.project_store.list()) == 2
    finally:
        runtime.close()


def test_detached_source_is_named_honestly_and_explained_before_automatic_start(tmp_path):
    service, runtime, _, workspace, source = build(tmp_path, [])
    try:
        git(workspace, "checkout", "--detach", "HEAD")
        overview = service.project_overview({"threadId": source})
        assert overview["workspace"]["branch"] == "detached HEAD"
        with pytest.raises(ValueError, match="source repository is in detached HEAD"):
            service.project_run_create({"operationId": str(uuid.uuid4()), "threadId": source,
                "objective": "Build the app", "tokenBudget": 1000, "maxEpisodes": 8,
                "reviewPolicy": "independent", "completionPolicy": "local_integrate"})
        assert service.project_store.list() == []
        assert git(workspace, "branch", "--show-current") == ""  # no hidden checkout
    finally:
        runtime.close()
