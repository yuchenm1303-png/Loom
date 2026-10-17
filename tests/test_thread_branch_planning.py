"""Task planning has a real model boundary and never copies the assistant reply."""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path

import pytest

from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.ai import AIEmptyResponseError, ModelResponse
from app.app_server import JsonRpcError
from app.app_server_thread_management import ManagedStreamingLoomAppServerService
from app.thread_branch_planning import parse_branch_plan


class PlanningPlatform:
    def __init__(self):
        self.requests = []
        self.response = json.dumps({
            "actionable": True,
            "title": "Repair CRLF tests",
            "summary": "Make the Files editor test portable across Windows and Linux.",
            "objective": "Replace hardcoded LF expectations in the Files editor test.",
            "acceptanceCriteria": [
                "Windows CRLF tests pass.",
                "LF tests still pass.",
            ],
            "constraints": ["Do not alter file contents."],
            "relevantFiles": ["tests/test_files.py"],
        })

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        return ModelResponse(text=self.response)


def _plan_result(service, parent_id, source):
    initial = service.thread_branch_plan({"parentThreadId": parent_id, "sourceText": source})
    assert initial["status"] == "pending"
    assert initial["planId"]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = service.thread_branch_plan_status({
            "parentThreadId": parent_id, "planId": initial["planId"],
        })
        if result["status"] != "pending":
            return result
        time.sleep(.015)
    raise AssertionError("planner did not finish")


@pytest.fixture()
def planned_service(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    platform = PlanningPlatform()
    runtime = DurableAgentRuntime(
        platform=platform, store=store, tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE, auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime, store=store, model="test",
        default_workspace=workspace, default_permission_mode=PermissionMode.WORKSPACE,
    )
    parent = service.thread_start({"workspace": str(workspace)})["thread"]
    yield service, platform, parent
    runtime.close()


def test_parser_accepts_structured_plan_and_compiles_executable_instructions():
    response = parse_branch_plan(json.dumps({
        "actionable": True, "title": "Fix CRLF",
        "summary": "Portable file test",
        "objective": "Fix the test without changing the editor.",
        "acceptanceCriteria": ["Windows passes", "Linux passes"],
        "constraints": ["No API changes"],
        "relevantFiles": ["tests/test_files.py"],
    }))
    assert response["title"] == "Fix CRLF"
    assert "Acceptance criteria" in response["prompt"]
    assert "Windows passes" in response["prompt"]
    assert "avoid overwriting concurrent edits" in response["prompt"]
    assert response["relevantFiles"] == ["tests/test_files.py"]


def test_non_actionable_and_invalid_plans_cannot_fabricate_tasks():
    assert parse_branch_plan('{"actionable":false,"reason":"Already finished"}') == {
        "actionable": False, "reason": "Already finished"
    }
    for bad in ("this answer is great", '{"actionable":true,"title":"Copy everything"}',
                '{"actionable":true,"title":"Fix", "objective":"Go","summary":"done","acceptanceCriteria":["One"]}'):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parse_branch_plan(bad)


def test_plan_rpc_uses_model_without_creating_child_or_tools(planned_service):
    service, platform, parent = planned_service
    response = _plan_result(service, parent["id"], "The Windows Files edit test is failing because of CRLF.")
    assert response["status"] == "ready"
    plan = response["proposal"]
    assert plan["title"] == "Repair CRLF tests"
    assert plan["summary"].startswith("Make the Files")
    assert len(plan["acceptanceCriteria"]) == 2
    assert len(service.thread_list({"view": "active"})["threads"]) == 1
    assert platform.requests
    profile_id, request = platform.requests[-1]
    assert request.tools == ()
    assert "The Windows Files edit test" in request.messages[-1].content
    assert request.max_output_tokens <= 800


def test_plan_errors_are_visible_and_do_not_copy_source(planned_service):
    service, platform, parent = planned_service
    platform.response = "Just paste the full answer and start"
    outcome = _plan_result(service, parent["id"], "Very long source")
    assert outcome["status"] == "error"
    assert "usable task" in outcome["error"]
    with pytest.raises(JsonRpcError):
        service.thread_branch_plan({"parentThreadId": parent["id"], "sourceText": "x" * 12001})
    with pytest.raises(JsonRpcError):
        service.thread_branch_plan({"parentThreadId": "missing", "sourceText": "text"})


def test_reviewed_plan_is_persisted_with_child(planned_service):
    service, _, parent = planned_service
    response = service.thread_branch_create({
        "parentThreadId": parent["id"], "title": "Fix portability",
        "summary": "Make Windows tests pass",
        "prompt": "Inspect the test and fix CRLF detection",
        "acceptanceCriteria": ["Windows pass", "Linux pass"],
        "startNow": False,
    })
    metadata = service.thread_library.read(response["thread"]["id"])
    assert metadata["branchSummary"] == "Make Windows tests pass"
    assert metadata["branchAcceptanceCriteria"] == ["Windows pass", "Linux pass"]
    with pytest.raises(JsonRpcError):
        service.thread_branch_create({
            "parentThreadId": parent["id"], "title": "Bad", "prompt": "Do it",
            "acceptanceCriteria": ["x" * 300], "startNow": False,
        })



def test_slow_planning_does_not_hold_up_normal_rpc(planned_service, monkeypatch):
    service, platform, parent = planned_service
    entered, release = threading.Event(), threading.Event()
    original = platform.execute_chat

    def slow_plan(profile_id, request):
        entered.set()
        assert release.wait(3), "test did not release the model"
        return original(profile_id, request)

    monkeypatch.setattr(platform, "execute_chat", slow_plan)
    ticket = service.thread_branch_plan({
        "parentThreadId": parent["id"], "sourceText": "Make this test portable.",
    })
    try:
        assert entered.wait(2), "planning worker never started"
        assert service.thread_branch_plan_status({
            "parentThreadId": parent["id"], "planId": ticket["planId"],
        })["status"] == "pending"
        # Planning must not hold the worker that serves other thread actions.
        another = service.thread_start({"workspace": parent["workspace"]})["thread"]
        assert another["id"] != parent["id"]
        with pytest.raises(JsonRpcError):
            service.thread_branch_plan_status({
                "parentThreadId": another["id"], "planId": ticket["planId"],
            })
        with pytest.raises(JsonRpcError):
            service.thread_branch_plan({
                "parentThreadId": parent["id"], "sourceText": "Duplicate planning",
            })
    finally:
        release.set()
    assert _eventually_ready(service, parent["id"], ticket["planId"])


def _eventually_ready(service, parent_id, plan_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.thread_branch_plan_status({
            "parentThreadId": parent_id, "planId": plan_id,
        })
        if result["status"] == "ready":
            return True
        time.sleep(.01)
    return False


def test_empty_model_output_has_plain_language_recovery_message(planned_service, monkeypatch):
    service, platform, parent = planned_service

    def no_text(_profile_id, _request):
        raise AIEmptyResponseError("reasoning-only completion")

    monkeypatch.setattr(platform, "execute_chat", no_text)
    result = _plan_result(service, parent["id"], "Review this focused failure.")
    assert result["status"] == "error"
    assert "Retry or create it manually" in result["error"]
    assert "AIEmptyResponseError" not in result["error"]
