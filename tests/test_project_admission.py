"""Host-wide admission (admission.py): several projects at once, bounded by slots and disk."""
import threading

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime import AgentTool, ToolEffect, ToolResult
from app.project_runner import admission, metrics, parallel
from test_project_runner import build, create, control, cycle, reply


PLAN = {"tasks": [{"instructions": "Write out.txt", "acceptance": ["out.txt exists"]}]}


def two_projects(tmp_path, *, meet):
    lock = threading.Lock()
    stats = {"active": 0, "peak": 0}
    meeting = threading.Barrier(2) if meet else None
    def write(context, args):
        with lock:
            stats["active"] += 1
            stats["peak"] = max(stats["peak"], stats["active"])
        try:
            if meeting:
                meeting.wait(12)  # both projects' workers are out at the same time
            context.resolve_workspace_path("out.txt").write_text(args["value"])
            return ToolResult(ok=True, content="saved")
        finally:
            with lock:
                stats["active"] -= 1
    tool = AgentTool(name="admission_test_write", description="Write the output", input_schema={"type": "object",
        "properties": {"value": {"type": "string"}}, "required": ["value"]}, effect=ToolEffect.MUTATING, handler=write)
    service, runtime, platform, workspace, first = build(tmp_path, [], tools=(tool,))
    second = service.thread_start({"workspaceDir": str(workspace), "permissionMode": "workspace"})["thread"]["id"]
    seen = {}
    def execute(_profile, request):
        platform.requests.append(request)
        run = service.project_store.session_run(request.session_id)
        with lock:
            index = seen.get(request.session_id, 0)
            seen[request.session_id] = index + 1
        if run["phase"] == "planning":
            return reply(PLAN)
        return (ModelResponse(tool_calls=(ToolCall(call_id=f"w-{run['id']}", name="admission_test_write",
                                                   arguments={"value": run["id"]}),))
                if index == 0 else reply({"action": "candidate", "summary": "written"}))
    platform.execute_chat = execute
    runs = [control(service, create(service, source), "start") for source in (first, second)]
    return service, runtime, [run["id"] for run in runs], stats


def finish(service, run_ids, limit=40):
    for _ in range(limit):
        cycle(service)
        if all(service.project_store.get(run_id)["state"] == "ready_for_review" for run_id in run_ids):
            return [service.project_store.get(run_id) for run_id in run_ids]
    raise AssertionError([service.project_store.get(run_id) for run_id in run_ids])


def test_two_conversations_projects_work_at_the_same_time(tmp_path):
    service, runtime, run_ids, stats = two_projects(tmp_path, meet=True)
    try:
        finish(service, run_ids)
        assert stats["peak"] == 2  # before: the second project queued until the first had nothing out
    finally:
        runtime.close()


def test_one_host_slot_serializes_projects_and_records_the_admission_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_PROJECT_MAX_EPISODES", "1")
    service, runtime, run_ids, stats = two_projects(tmp_path, meet=False)
    try:
        finish(service, run_ids)
        assert stats["peak"] == 1
        waited = metrics.waits(service.project_store, run_ids)
        assert waited.get("admission", 0) > 0
    finally:
        runtime.close()


def test_slots_are_configured_bounded_and_halve_while_a_provider_recovers(monkeypatch):
    now = 1000.0
    assert admission.slots([], now=now) == admission.DEFAULT_SLOTS
    for value, expected in (("8", 8), ("0", 1), ("99", admission.MAX_SLOTS), ("nonsense", admission.DEFAULT_SLOTS)):
        monkeypatch.setenv("LOOM_PROJECT_MAX_EPISODES", value)
        assert admission.slots([], now=now) == expected
    monkeypatch.setenv("LOOM_PROJECT_MAX_EPISODES", "6")
    waiting = {"retryAfter": now + 30, "transientFailureStreak": 1}
    assert admission.slots([waiting], now=now) == 3
    assert admission.slots([{**waiting, "retryAfter": now - 1}], now=now) == 6  # outage over
    assert admission.slots([{**waiting, "transientFailureStreak": 0}], now=now) == 6  # a credential wait, not a throttle


def test_family_bounds_still_hold_under_more_slots():
    class Store:
        def get(self, run_id):
            return parent
    parent = {"id": "p", "state": "running", "control": "active", "phase": "parallel", "maxWorkers": 2,
              "teamChildren": [{"runId": "c3", "taskId": "task-3", "state": "running"}]}
    child = {"id": "c3", "teamOwnerId": "p"}
    others = [{"id": "x"}, {"id": "y"}]
    own = [{"id": "c1", "teamOwnerId": "p"}, {"id": "c2", "teamOwnerId": "p"}]
    assert parallel.eligible(Store(), child, others, slots=4)          # another project's work no longer blocks
    assert not parallel.eligible(Store(), child, own, slots=4)         # but the team's own two workers do
    assert not parallel.eligible(Store(), child, others + [own[0]], slots=3)  # and the Host's slots
    root = {"id": "r", "state": "running", "control": "active", "phase": "working"}
    assert parallel.eligible(Store(), root, others, slots=4)
    assert not parallel.eligible(Store(), root, [{"id": "r"}], slots=4)


def test_checkouts_that_fit_leave_the_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(admission, "checkout_size", lambda source: 100 * 1024 ** 2)
    need = int(100 * 1024 ** 2 * admission.CHECKOUT_OVERHEAD)
    assert admission.checkouts_that_fit(tmp_path, tmp_path, 4, free=admission.RESERVE_BYTES + 3 * need) == 3
    assert admission.checkouts_that_fit(tmp_path, tmp_path, 4, free=admission.RESERVE_BYTES - 1) == 0
    assert admission.checkouts_that_fit(tmp_path, tmp_path, 2, free=10 ** 15) == 2
    assert admission.checkouts_that_fit(tmp_path, tmp_path, 0, free=10 ** 15) == 0


def test_a_full_drive_holds_team_tasks_until_there_is_room(tmp_path, monkeypatch):
    from test_project_parallel import setup, until
    room = {"value": 0}
    monkeypatch.setattr(admission, "checkouts_that_fit", lambda home, source, wanted, free=None: min(wanted, room["value"]))
    service, runtime, _, _, run_id, _ = setup(tmp_path, barrier=False)
    try:
        held = until(service, run_id, lambda r: r["phase"] == "parallel")
        for _ in range(3):
            cycle(service)
        assert service.project_store.get(run_id).get("teamChildren", []) == []
        assert service.project_store._open_waits.get(f"{run_id}:team", ("",))[0] == "disk"
        room["value"] = 4
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert len(final["teamChildren"]) == 2 and "disk" in metrics.waits(service.project_store, [run_id])
        del held
    finally:
        runtime.close()
