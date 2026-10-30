"""Replay, in one scripted project, the failure modes real-model runs actually hit.

Each of these once stopped a real project (see docs/project-runner-live-qualification):
a reply without JSON, a provider connection error, a reply followed by text, reviewer
test output left in its checkout, and a pass filed with static evidence. Together they
must now cost one bounded recovery each and the project must still pass, without ever
stopping for inspection.
"""
import json
import time as real_time

from app.ai import ModelResponse, ModelUsage
from app.ai.errors import AITransportError
from app.agent_runtime.contracts import AgentLimits
from app.project_runner import runner as runner_module
from test_project_runner import create, control, cycle, reply
from test_project_review import (setup_review, writing, checking, check_both, CANDIDATE, PASSED, PASSED_ALL,
                                 TWO_TASKS)


class Clock:
    """Each read is an hour later, so a provider backoff has always elapsed by the next tick."""
    def __init__(self):
        self.now = real_time.time()

    def time(self):
        self.now += 3600
        return self.now


def test_one_project_survives_every_recorded_real_failure_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "time", Clock())
    prose = ModelResponse(text="Here is the plan, as discussed.", usage=ModelUsage(5, 0, 5))
    noted = ModelResponse(text=json.dumps(CANDIDATE) + "\n\nRuntime verification: earlier changes still lack current "
                          "acceptance evidence and remain unverified.", usage=ModelUsage(5, 0, 5))
    leaves_artifact = checking(code="from pathlib import Path; Path('reviewer-output.log').write_text('ran'); "
                                    "assert Path('app.txt').read_text() == 'fixed'")
    static_pass = check_both()
    for call in static_pass.tool_calls:
        call.arguments["verification"]["check_kind"] = "static"
    responses = [
        prose, reply(TWO_TASKS),                                   # planner: no JSON, then a plan
        writing("fixed"), noted,                                   # task-1 worker, after one connection error
        leaves_artifact,                                           # task-1 review leaves output in its checkout,
        reply({"verdict": "continue", "summary": "More next episode"}), reply(PASSED),  # and continues over episodes
        reply(CANDIDATE),                                          # task-2 worker, the last task
        static_pass, reply(PASSED_ALL), check_both(), reply(PASSED_ALL),  # whole-project review, static then real
    ]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    runtime.limits = AgentLimits(transport_retries=0)
    original = platform.execute_chat
    calls = []
    def execute(profile, request):
        calls.append(request)
        if len(calls) == 3:  # the task-1 worker's first request
            platform.requests.append(request)
            raise AITransportError("AI request failed via provider 'p': APIConnectionError: Connection error.")
        return original(profile, request)
    platform.execute_chat = execute
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=20, tokenBudget=200000)
        control(service, run, "start")
        states = []
        for _ in range(24):
            cycle(service)
            current = service.project_store.get(run["id"])
            states.append(current["state"])
            if current["state"] != "running" and not current["activeEpisode"]:
                break
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review" and final["verificationStatus"] == "passed", final["reason"]
        assert "needs_review" not in states
        kinds = [item["kind"] for item in service.project_store.events(run["id"], limit=200)]
        for recovery in ("reply_rejected", "transient_failure_retry", "review_evidence_rejected"):
            assert kinds.count(recovery) == 1, (recovery, kinds)
        assert "review_invalidated" not in kinds  # the reviewer's output file did not reopen task-1
        for counter, _, _ in runner_module.RECOVERY.values():
            assert final.get(counter, 0) == 0
        assert len(platform.requests) == len(responses) + 1  # every response used, plus the failed request
    finally:
        runtime.close()


def test_only_tools_confined_to_the_checkout_let_an_interrupted_episode_continue():
    from types import SimpleNamespace
    from app.agent_runtime.contracts import AgentEventKind as Kind
    from app.project_runner.service import confined_in_flight

    def event(kind, **data):
        return SimpleNamespace(kind=kind, data=data)
    workspace = {"enforced": True, "mode": "workspace", "network_isolated": True}
    def turn(tool, sandbox=None, effect="sensitive", finished=False):
        events = [event(Kind.TOOL_STARTED, call_id="c", tool=tool, effect=effect)]
        if sandbox is not None:
            events.append(event(Kind.PROCESS_STARTED, sandbox=sandbox))
        if finished:
            events.append(event(Kind.TOOL_COMPLETED, call_id="c"))
        return events
    assert confined_in_flight(turn("exec", workspace)) == ["exec"]
    assert confined_in_flight(turn("exec")) == ["exec"]  # no process was launched yet
    assert confined_in_flight(turn("exec", {**workspace, "network_isolated": False})) is None
    assert confined_in_flight(turn("exec", {**workspace, "enforced": False})) is None
    assert confined_in_flight(turn("exec", {**workspace, "mode": "full-access"})) is None
    assert confined_in_flight(turn("apply_patch", effect="mutating")) == ["apply_patch"]
    assert confined_in_flight(turn("mcp_issue_tracker_write", effect="mutating")) is None
    assert confined_in_flight(turn("web_search", effect="read_only")) == ["web_search"]
    assert confined_in_flight(turn("mcp_issue_tracker_write", effect="mutating", finished=True)) == []
