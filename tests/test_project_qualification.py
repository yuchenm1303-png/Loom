"""Qualification oracle and cleanup must not turn provider failures into passes."""
from pathlib import Path
import threading
import json
import os
from types import SimpleNamespace

import pytest

from scripts.smoke_project_runner import oracle, failure_kind, cancel_run, settle_usage
from test_project_runner import build, create, control, cycle, reply, PLAN
import scripts.smoke_project_runner as qualification


def test_configured_opencode_adapter_is_accepted_by_live_runtime_parser():
    from loom_cli import build_parser
    assert build_parser().parse_args(["--provider", "opencode-go", "--model", "deepseek-v4.1-flash"]).provider == "opencode-go"


def test_external_oracle_rejects_a_plausible_but_incorrect_cli(tmp_path):
    (tmp_path / "tally.py").write_text('print(\'{"sum":4,"unique":2}\')\n', encoding="utf-8")
    results = oracle(tmp_path)
    assert results[0]["passed"]
    assert not all(item["passed"] for item in results)
    assert len(results) == 8


def test_external_oracle_accepts_valid_cli_and_checks_invalid_input(tmp_path):
    (tmp_path / "tally.py").write_text('''import json,sys
try:
 if len(sys.argv)!=2 or not sys.argv[1].strip():raise ValueError()
 values=[int(value.strip()) for value in sys.argv[1].split(',')]
except ValueError:sys.exit(2)
print(json.dumps({'sum':sum(values),'unique':len(set(values))}))
''', encoding="utf-8")
    assert all(item["passed"] for item in oracle(tmp_path))


@pytest.mark.parametrize("reason,expected", [
    ("AITransportError: Error 402 Insufficient Balance", "provider_balance"),
    ("HTTP 401 unauthorized", "provider_authentication"),
    ("HTTP 401 api_key_expired", "provider_key_expired"),
    ("HTTP 429 too many requests", "provider_rate_limit"),
    ("Missing independent acceptance evidence", "project_blocked"),
])
def test_provider_failure_classification_has_no_raw_payload(reason, expected):
    assert failure_kind(reason) == expected


def test_cleanup_serializes_revision_read_with_scheduler_updates(tmp_path, monkeypatch):
    service, runtime, _, _, source = build(tmp_path, [])
    run = create(service, source, completionPolicy="local_integrate", reviewPolicy="independent")
    reading, attempted, done = threading.Event(), threading.Event(), threading.Event()
    original = service.project_store.get
    once = []
    def read(run_id):
        result = original(run_id)
        if not once:
            once.append(True)
            reading.set()
            assert attempted.wait(5)
        return result
    def settle():
        assert reading.wait(5)
        attempted.set()
        with service.project_runner.guard:
            with service.project_store.transaction() as db:
                current = service.project_store._read(db, run["id"])
                current["tokensUsed"] += 7
                service.project_store.save(db, current, "late_usage")
        done.set()
    monkeypatch.setattr(service.project_store, "get", read)
    worker = threading.Thread(target=settle)
    worker.start()
    try:
        assert cancel_run(service, run["id"])
        assert done.wait(5)
        result = original(run["id"])
        assert result["state"] == "cancelled" and result["tokensUsed"] == 7
        assert not cancel_run(service, run["id"])
    finally:
        worker.join(timeout=5)
        runtime.close()


def test_live_driver_preserves_balance_failure_and_cleans_up_without_leaking_secrets(tmp_path, monkeypatch, capsys):
    from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, ToolRegistry
    from app.project_runner.store import ProjectStore
    class Config:
        def get(self, model_id):
            return SimpleNamespace(model="unit-model", adapter=SimpleNamespace(value="openai-compatible"), base_url="https://example.invalid")
        def secret_for(self, entry):
            return "unit-configured-secret"
    class Unavailable:
        def execute_chat(self, profile, request):
            raise RuntimeError("HTTP 402 Insufficient Balance")
    def runtime_factory(args):
        assert os.environ["LOOM_API_KEY"] == "unit-configured-secret"
        assert "PYTHONPATH" not in os.environ
        store = FileAgentSessionStore(Path(args.home))
        runtime = DurableAgentRuntime(platform=Unavailable(), store=store, tools=ToolRegistry(()))
        return runtime, store, args.model
    monkeypatch.setattr(qualification, "ModelConfigStore", Config)
    monkeypatch.setattr(qualification, "_build_runtime", runtime_factory)
    monkeypatch.setattr(qualification, "probe_workspace_sandbox", lambda workspace: {"backend": "offline-test", "enforced": True})
    monkeypatch.setenv("LOOM_API_KEY", "outer-env-sentinel")
    monkeypatch.setenv("PYTHONPATH", "outer-pythonpath-sentinel")
    root = tmp_path / "qualification"
    assert qualification.main(["--root", str(root), "--stored-model", "unit", "--seconds", "20"]) == 1
    assert os.environ["LOOM_API_KEY"] == "outer-env-sentinel"
    assert os.environ["PYTHONPATH"] == "outer-pythonpath-sentinel"
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "blocked" and report["failureKind"] == "provider_balance"
    assert report["cancellationRequested"] is True
    assert "errorType" not in report
    store = ProjectStore(root / "state" / "project-runner")
    try:
        assert store.get(report["runId"])["state"] == "cancelled"
    finally:
        store.close()
    assert "unit-configured-secret" not in capsys.readouterr().out


def test_missing_sandbox_stops_before_credentials_or_model_calls(tmp_path, monkeypatch):
    def unavailable(workspace):
        raise ValueError("Workspace sandbox unavailable")
    def forbidden():
        pytest.fail("Should not read credentials without a usable sandbox")
    monkeypatch.setattr(qualification, "probe_workspace_sandbox", unavailable)
    monkeypatch.setattr(qualification, "ModelConfigStore", forbidden)
    with pytest.raises(SystemExit) as error:
        qualification.main(["--root", str(tmp_path / "new"), "--stored-model", "unused"])
    assert error.value.code == 2
    assert not (tmp_path / "new").exists()


def test_probe_exercises_interpreter_without_forwarding_development_import_paths(tmp_path, monkeypatch):
    called = []
    class Manager:
        def snapshot(self, **kwargs):
            return SimpleNamespace(enforced=True, backend=SimpleNamespace(value="fixture"), network_isolated=True)
        def prepare(self, **kwargs):
            called.append(kwargs)
            return SimpleNamespace(argv=kwargs["argv"], cwd=kwargs["cwd"])
    monkeypatch.setattr(qualification, "SandboxManager", Manager)
    monkeypatch.setenv("PYTHONPATH", "must-not-be-forwarded")
    result = qualification.probe_workspace_sandbox(tmp_path / "future")
    assert result["backend"] == "fixture"
    assert "PYTHONPATH" not in called[0]["environment"]


def test_cancelled_approval_usage_settles_once_without_dispatching_more_models(tmp_path):
    from app.ai import ModelResponse, ModelUsage, ToolCall
    from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
    tool = AgentTool(name="qualification_write", description="Requires approval", input_schema={"type": "object"},
        effect=ToolEffect.MUTATING, handler=lambda context, args: ToolResult(ok=True, content="not executed"))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN), ModelResponse(
        tool_calls=(ToolCall(call_id="approval-call", name=tool.name, arguments={}),), usage=ModelUsage(11, 0, 11))], tools=(tool,))
    try:
        runtime.set_permission_mode(source, PermissionMode.APPROVAL)
        run = create(service, source, completionPolicy="local_integrate", reviewPolicy="independent")
        control(service, run, "start")
        for _ in range(5):
            cycle(service)
            run = service.project_store.get(run["id"])
            if run.get("activeEpisode", {}) and run["activeEpisode"]["state"] == "waiting_approval":
                break
        assert run["activeEpisode"]["state"] == "waiting_approval"
        assert run["tokensUsed"] == 5
        assert cancel_run(service, run["id"])
        result = settle_usage(service, run["id"])
        assert result["tokensUsed"] == 16 and result["unsettledEpisodes"] == 0
        assert settle_usage(service, run["id"])["tokensUsed"] == 16
        assert len(platform.requests) == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("limit", ["max_model_steps", "max_tool_calls"])
def test_runtime_limits_are_known_terminal_outcomes_with_usage_settled_once(tmp_path, limit):
    from app.ai import ModelResponse, ModelUsage, ToolCall
    from app.agent_runtime import AgentTool, ToolEffect, ToolResult
    from app.agent_runtime.contracts import AgentLimits
    tool = AgentTool(name="qualification_read", description="Read fixture", input_schema={"type": "object"},
        effect=ToolEffect.READ_ONLY, handler=lambda context, args: ToolResult(ok=True, content="fixture"))
    response = ModelResponse(tool_calls=tuple(ToolCall(call_id=f"read-{i}", name=tool.name, arguments={}) for i in range(2)), usage=ModelUsage(11, 0, 11))
    service, runtime, platform, _, source = build(tmp_path, [response], tools=(tool,))
    try:
        runtime.limits = AgentLimits(**{limit: 1})
        run = create(service, source)
        control(service, run, "start")
        for _ in range(4):
            cycle(service)
            run = service.project_store.get(run["id"])
            if run["state"] == "needs_review":
                break
        assert run["activeEpisode"] is None
        assert run["tokensUsed"] == 11 and run["episodesUsed"] == 1
        assert "limit" in run["reason"].lower() and "unknown" not in run["reason"].lower()
        for _ in range(2):
            cycle(service)
        assert service.project_store.get(run["id"])["tokensUsed"] == 11
        assert len(platform.requests) == 1
    finally:
        runtime.close()


def test_qualification_budget_stops_following_requests_and_deduplicates_usage():
    from app.agent_runtime.contracts import AgentEventKind as E
    cancelled = []
    guard = qualification.RequestBudgetGuard(SimpleNamespace(cancel=cancelled.append), 25)
    def event(kind, event_id, tokens=0):
        return SimpleNamespace(kind=kind, event_id=event_id, session_id="test-session", data={"usage": {"total_tokens": tokens}})
    guard.observe(event(E.MODEL_RESPONSE, "first", 10))
    assert not cancelled
    guard.observe(event(E.MODEL_RESPONSE, "second", 20))
    assert guard.stopped and guard.tokens == 30
    guard.observe(event(E.MODEL_RESPONSE, "second", 20))
    guard.observe(event(E.MODEL_REQUESTED, "next"))
    assert guard.tokens == 30
    assert len(cancelled) == 3
