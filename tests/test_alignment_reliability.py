from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest

from app.agent_runtime import (
    AgentRuntime, CoreAgentRuntime, AgentLimits, AgentStatus, AgentTool,
    FileAgentSessionStore, SandboxManager, SandboxPolicy, ToolRegistry, ToolResult,
    ToolEffect,
)
from app.ai import AIMessage, ImagePart, ModelResponse, ToolCall, MessageRole, TextPart
from app.agent_runtime.instructions import InstructionLoader
from app.agent_runtime.context_budget import safe_split
from app.agent_runtime.tools import validate_tool_arguments


class Scripted:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        return next(self.responses)


def make_runtime(path, platform, tools=(), **kwargs):
    return AgentRuntime(platform=platform, store=FileAgentSessionStore(path),
        tools=ToolRegistry(tuple(tools)), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF), **kwargs)


@pytest.mark.parametrize("kind", [AgentRuntime, CoreAgentRuntime])
def test_resubmitting_identical_failed_input_reuses_canonical_user_message(tmp_path, kind):
    class FailThenSucceed:
        def __init__(self):
            self.calls = 0

        def execute_chat(self, profile, request):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider rejected request")
            return ModelResponse(text="recovered")

    platform = FailThenSucceed()
    kwargs = {
        "platform": platform,
        "store": FileAgentSessionStore(tmp_path),
        "tools": ToolRegistry(),
    }
    runtime = kind(**kwargs)
    session = runtime.create_session("agent.fast")
    content = (
        TextPart("same request"),
        ImagePart("data:image/png;base64,AA"),
    )

    assert runtime.start_turn(session.session_id, content).status is AgentStatus.FAILED
    assert runtime.start_turn(session.session_id, content).status is AgentStatus.COMPLETED

    stored = runtime.store.load(session.session_id)
    assert sum(message.role is MessageRole.USER for message in stored.messages) == 1
    user_events = [event for event in runtime.store.events(session.session_id)
                   if event.kind.value == "user_message"]
    assert len(user_events) == 1
    starts = [event for event in runtime.store.events(session.session_id)
              if event.kind.value == "turn_started"]
    assert starts[-1].data["retrying_failed_input"] is True
    runtime.close()


@pytest.mark.parametrize("kind", [AgentRuntime, CoreAgentRuntime])
@pytest.mark.parametrize("reason", ["length", "content_filter", "incomplete", "unknown"])
def test_partial_response_never_completes_or_executes(tmp_path, kind, reason):
    calls = []
    tool = AgentTool("touch", "touch", {"type": "object"}, lambda c, a: calls.append(1))
    runtime = kind(platform=Scripted([ModelResponse(text="partial", finish_reason=reason,
        tool_calls=(ToolCall("x", "touch", {}),))]), store=FileAgentSessionStore(tmp_path), tools=ToolRegistry((tool,)))
    session = runtime.create_session("agent.fast")
    result = runtime.start_turn(session.session_id, "work")
    assert result.status is AgentStatus.FAILED
    assert not calls
    assert reason in result.error
    runtime.close()


@pytest.mark.parametrize("fragment", ["继续诊断：[", "next: {", "```json"])
def test_dangling_terminal_response_is_retried_without_poisoning_history(tmp_path, fragment):
    platform = Scripted([
        ModelResponse(
            text=f"<think>I should call a tool next.</think>\n{fragment}",
            finish_reason="stop",
        ),
        ModelResponse(text="诊断已完成。", finish_reason="stop"),
    ])
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "继续诊断")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "诊断已完成。"
    stored = runtime.store.load(session.session_id)
    assert all(fragment not in str(message.content) for message in stored.messages)
    assert platform.requests[-1].messages[-1].name == "loom_terminal_recovery"
    rejected = [
        event for event in runtime.store.events(session.session_id)
        if event.kind.value == "model_response_rejected"
    ]
    assert rejected[-1].data["reason"] in {
        "dangling_serialized_structure",
        "unterminated_code_fence",
    }
    runtime.close()


def test_repeated_invalid_terminal_response_fails_with_diagnostic(tmp_path):
    runtime = make_runtime(
        tmp_path,
        Scripted([ModelResponse(text="继续：[", finish_reason="stop") for _ in range(3)]),
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "继续")

    assert result.status is AgentStatus.FAILED
    assert "invalid terminal response" in result.error
    stored = runtime.store.load(session.session_id)
    assert all("继续：[" not in str(message.content) for message in stored.messages)
    runtime.close()


def test_empty_completed_response_is_retried_without_poisoning_history(tmp_path):
    from app.ai.errors import AIEmptyResponseError

    class EmptyThenComplete:
        def __init__(self):
            self.requests = []

        def execute_chat(self, profile, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise AIEmptyResponseError(
                    "reasoning-only",
                    finish_reason="stop",
                    response_id="empty-1",
                    reasoning_char_count=17,
                    chunk_count=3,
                    input_tokens=8,
                    output_tokens=2,
                    total_tokens=10,
                )
            return ModelResponse(text="诊断继续完成。", finish_reason="stop")

    platform = EmptyThenComplete()
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "继续诊断")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "诊断继续完成。"
    assert platform.requests[-1].messages[-1].name == "loom_terminal_recovery"
    stored = runtime.store.load(session.session_id)
    assert stored.usage.total_tokens == 10
    rejected = [e for e in runtime.store.events(session.session_id)
                if e.kind.value == "model_response_rejected"]
    assert rejected[-1].data["reason"] == "reasoning_only_response"
    assert rejected[-1].data["reasoning_char_count"] == 17
    assert rejected[-1].data["stream_chunk_count"] == 3
    runtime.close()


def test_repeated_empty_completed_response_fails_diagnostically(tmp_path):
    from app.ai.errors import AIEmptyResponseError

    class AlwaysEmpty:
        def execute_chat(self, profile, request):
            raise AIEmptyResponseError("empty", finish_reason="stop", chunk_count=1)

    runtime = make_runtime(tmp_path, AlwaysEmpty())
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "继续")

    assert result.status is AgentStatus.FAILED
    assert "repeatedly completed without public text or tool calls" in result.error
    assert len([e for e in runtime.store.events(session.session_id)
                if e.kind.value == "model_response_rejected"]) == 3
    runtime.close()


@pytest.mark.parametrize("message", [
    "tool call 'exec' arguments must be a JSON object",
    "tool call 'exec' returned invalid JSON arguments",
    "streamed tool call is missing id or function name",
])
def test_malformed_provider_response_is_retried_as_one_error_family(tmp_path, message):
    from app.ai.errors import AIResponseError

    class MalformedThenComplete:
        def __init__(self):
            self.requests = []

        def execute_chat(self, profile, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise AIResponseError(message)
            return ModelResponse(text="recovered", finish_reason="stop")

    platform = MalformedThenComplete()
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "recovered"
    assert platform.requests[-1].messages[-1].name == "loom_terminal_recovery"
    rejected = [e for e in runtime.store.events(session.session_id)
                if e.kind.value == "model_response_rejected"]
    assert rejected[-1].data["reason"] == "invalid_provider_response"
    assert rejected[-1].data["error"] == message
    assert not any(message in str(m.content) for m in runtime.store.load(session.session_id).messages)
    runtime.close()


def test_cancel_returns_turn_without_waiting_for_blocked_model(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class Blocked:
        def execute_chat(self, profile, request):
            entered.set()
            release.wait(5)
            return ModelResponse(text="must not commit")
    rt = make_runtime(tmp_path, Blocked())
    session = rt.create_session("agent.fast")
    results = []
    worker = threading.Thread(target=lambda: results.append(rt.start_turn(session.session_id, "work")))
    worker.start()
    assert entered.wait(3)
    rt.cancel(session.session_id)
    worker.join(1)
    try:
        assert not worker.is_alive()
        assert results[0].status is AgentStatus.CANCELLED
        assert not any(m.role is MessageRole.ASSISTANT for m in rt.store.load(session.session_id).messages)
    finally:
        release.set()
        worker.join(5)
        rt.close()


def test_approval_binding_survives_restart_but_rejects_replacement(tmp_path):
    executed = []
    def handler(c, a):
        executed.append("original")
        return ToolResult(True, "done")
    tool = AgentTool("change", "original", {"type": "object"}, handler, effect=ToolEffect.MUTATING)
    first = make_runtime(tmp_path, Scripted([ModelResponse(tool_calls=(ToolCall("call", "change", {}),))]), [tool])
    session = first.create_session("agent.fast")
    assert first.start_turn(session.session_id, "work").status is AgentStatus.WAITING_APPROVAL
    first.close()
    changed = make_runtime(tmp_path, Scripted([ModelResponse(text="done")]), [replace(tool, binding_key="new-endpoint")])
    with pytest.raises(ValueError, match="binding changed"):
        changed.resume_approval(session.session_id, "call", approved=True)
    assert not executed
    changed.close()
    restored = make_runtime(tmp_path, Scripted([ModelResponse(text="done")]), [tool])
    assert restored.resume_approval(session.session_id, "call", approved=True).status is AgentStatus.COMPLETED
    assert executed == ["original"]
    restored.close()


def test_commit_recovers_snapshot_after_event_write_failure(tmp_path, monkeypatch):
    rt = make_runtime(tmp_path, Scripted([]))
    session = rt.create_session("agent.fast")
    original = rt.store._save
    def broken(session):
        raise OSError("injected disk failure")
    monkeypatch.setattr(rt.store, "_save", broken)
    session.final_text = "committed state"
    from app.agent_runtime import AgentEventKind
    with pytest.raises(OSError):
        rt._record(session, AgentEventKind.TURN_COMPLETED, data={"text": "committed state"})
    restarted = FileAgentSessionStore(tmp_path)
    assert restarted.load(session.session_id).final_text == "committed state"
    events = restarted.events(session.session_id)
    assert sum(e.kind is AgentEventKind.TURN_COMPLETED for e in events) == 1
    monkeypatch.setattr(rt.store, "_save", original)
    rt.close()


def test_torn_log_tail_is_ignored_and_repaired_on_next_append(tmp_path):
    rt = make_runtime(tmp_path, Scripted([ModelResponse(text="done")]))
    session = rt.create_session("agent.fast")
    path = rt.store.session_dir(session.session_id) / "events.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"event_id": "broken')
    assert len(rt.store.events(session.session_id)) == 1
    assert rt.start_turn(session.session_id, "work").status is AgentStatus.COMPLETED
    assert all(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    rt.close()


def test_project_rules_nested_override_and_actual_request(tmp_path):
    root = tmp_path / "repo"
    nested = root / "sub"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("ROOT RULE", encoding="utf-8")
    (nested / "AGENTS.md").write_text("SHADOWED RULE", encoding="utf-8")
    (nested / "AGENTS.override.md").write_text("CHILD RULE", encoding="utf-8")
    p = Scripted([ModelResponse(text="done")])
    rt = make_runtime(tmp_path / "state", p)
    session = rt.create_session("agent.fast", workspace_dir=nested)
    rt.start_turn(session.session_id, "work")
    text = "\n".join(str(m.content) for m in p.requests[0].messages)
    assert text.index("ROOT RULE") < text.index("CHILD RULE")
    assert "SHADOWED RULE" not in text
    rt.close()


def test_auto_compaction_continues_same_turn(tmp_path):
    p = Scripted([ModelResponse(text="prior work summary"), ModelResponse(text="finished")])
    rt = make_runtime(tmp_path, p, limits=AgentLimits(max_messages=10))
    session = rt.create_session("agent.fast")
    for i in range(8):
        session.messages += [AIMessage(role=MessageRole.USER, content=f"task {i}"), AIMessage(role=MessageRole.ASSISTANT, content=f"answer {i}")]
    rt.store.save(session)
    result = rt.start_turn(session.session_id, "IMPORTANT CURRENT CONSTRAINT")
    assert result.status is AgentStatus.COMPLETED
    assert len(rt.list_context_checkpoints(session.session_id)) == 1
    assert any(m.content == "IMPORTANT CURRENT CONSTRAINT" for m in p.requests[-1].messages)
    rt.close()


def test_safe_split_does_not_orphan_parallel_tool_outputs():
    messages = [AIMessage(role=MessageRole.USER, content="constraint")]
    for i in range(8):
        calls = (ToolCall(f"a{i}", "read", {}), ToolCall(f"b{i}", "read", {}))
        messages.append(AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=calls))
        messages.extend(AIMessage(role=MessageRole.TOOL, content="ok", tool_call_id=c.call_id) for c in calls)
    split, user = safe_split(messages)
    assert split > 0 and user == 0
    assert messages[split].role is MessageRole.ASSISTANT


def test_standard_json_schema_refs_and_bounds():
    schema = {"type": "object", "$defs": {"n": {"type": "integer", "minimum": 1}},
        "properties": {"n": {"$ref": "#/$defs/n"}}, "required": ["n"]}
    validate_tool_arguments(schema, {"n": 1})
    with pytest.raises(ValueError):
        validate_tool_arguments(schema, {"n": -1})


def test_steering_is_durable_and_prevents_stale_tool_execution(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls, requests = [], []
    class P:
        def execute_chat(self, profile, request):
            requests.append(request)
            if len(requests) == 1:
                entered.set()
                release.wait(5)
                return ModelResponse(tool_calls=(ToolCall("old", "touch", {}),))
            return ModelResponse(text="followed new direction")
    tool = AgentTool("touch", "old action", {"type": "object"}, lambda c, a: calls.append(1))
    rt = make_runtime(tmp_path, P(), [tool])
    session = rt.create_session("agent.fast")
    result = []
    worker = threading.Thread(target=lambda: result.append(rt.start_turn(session.session_id, "old direction")))
    worker.start()
    assert entered.wait(3)
    turn_id = rt.store.load(session.session_id).current_turn_id
    try:
        with pytest.raises(ValueError):
            rt.steer(session.session_id, "wrong", turn_id="stale")
        rt.steer(session.session_id, "NEW DIRECTION", turn_id=turn_id)
        assert FileAgentSessionStore(tmp_path).pending_steering(session.session_id, turn_id)
    finally:
        release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert result[0].status is AgentStatus.COMPLETED
    assert not calls
    assert any(m.content == "NEW DIRECTION" for m in requests[-1].messages)
    assert not rt.store.pending_steering(session.session_id, turn_id)
    rt.close()


def test_stream_retry_does_not_duplicate_tool_side_effect(tmp_path):
    from app.ai.errors import AITransportError
    class P:
        count = 0
        def execute_chat(self, profile, request):
            self.count += 1
            if self.count == 1:
                raise AITransportError("connection lost")
            if self.count == 2:
                return ModelResponse(tool_calls=(ToolCall("call", "effect", {}),))
            return ModelResponse(text="done")
    calls = []
    def effect(c, a):
        calls.append(1)
        return ToolResult(True, "done")
    rt = make_runtime(tmp_path, P(), [AgentTool("effect", "test", {"type": "object"}, effect)])
    session = rt.create_session("agent.fast")
    assert rt.start_turn(session.session_id, "work").status is AgentStatus.COMPLETED
    assert calls == [1]
    rt.close()


def test_permanent_transport_failure_is_not_retried_by_turn_runner(tmp_path):
    from app.ai.errors import AITransportError

    class P:
        count = 0

        def execute_chat(self, profile, request):
            self.count += 1
            raise AITransportError("402 Insufficient Balance", retryable=False)

    platform = P()
    rt = make_runtime(tmp_path, platform)
    session = rt.create_session("agent.fast")

    result = rt.start_turn(session.session_id, "work")

    assert result.status is AgentStatus.FAILED
    assert platform.count == 1
    assert "Insufficient Balance" in result.error
    rt.close()


def test_shell_policy_filters_inherited_and_explicit_variables(monkeypatch):
    from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
    monkeypatch.setenv("LOOM_TEST_VISIBLE", "yes")
    monkeypatch.setenv("LOOM_TEST_SECRET", "hidden")
    policy = ShellEnvironmentPolicy(include_only=("LOOM_TEST_*",), exclude=("*DENIED*",))
    assert policy.build() == {"LOOM_TEST_VISIBLE": "yes"}
    with pytest.raises(ValueError):
        policy.build({"LOOM_TEST_DENIED": "no"})
    with pytest.raises(ValueError):
        policy.build({"LOOM_TEST_API_KEY": "no"})


def test_notification_overflow_delivers_resync_after_draining():
    import io
    from app.app_server import JsonRpcStdioServer
    class Service:
        def subscribe_notifications(self, callback):
            self.callback = callback
    server = JsonRpcStdioServer(Service(), outbound_limit=4)
    server.controller.initialized = True
    server._writer = io.StringIO()
    for index in range(5):
        server._on_notification("item/delta", {"threadId": "thread", "index": index})
    worker = threading.Thread(target=server._writer_loop)
    worker.start()
    deadline = time.monotonic() + 2
    while "thread/resync" not in server._writer.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    server._outbound.put(server._STOP)
    worker.join(2)
    frames = [json.loads(line) for line in server._writer.getvalue().splitlines()]
    assert frames[-1]["method"] == "thread/resync"
    assert frames[-1]["params"]["threadId"] == "thread"


def test_text_patch_updates_moves_and_adds_atomically(tmp_path):
    from app.agent_runtime.patch_tools import apply_patch_tool
    from app.agent_runtime.tools import ToolContext
    from app.agent_runtime.diff_tracker import TurnDiffTracker
    (tmp_path / "old.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    context = ToolContext("session", "turn", tmp_path,
        services={"diff_tracker": TurnDiffTracker()})
    patch = "*** Begin Patch\n*** Update File: old.py\n*** Move to: new.py\n@@\n def run():\n-    return 1\n+    return 2\n*** Add File: note.txt\n+done\n*** End Patch"
    tool = apply_patch_tool()
    validate_tool_arguments(tool.input_schema, {"patch": patch})
    assert tool.handler(context, {"patch": patch}).ok
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "new.py").read_text() == "def run():\n    return 2\n"
    bad = "*** Begin Patch\n*** Add File: first.txt\n+must not commit\n*** Update File: new.py\n@@\n-missing\n+bad\n*** End Patch"
    with pytest.raises(ValueError):
        tool.handler(context, {"patch": bad})
    assert not (tmp_path / "first.txt").exists()


def test_loaded_skill_snapshot_survives_compaction_and_restart(tmp_path):
    root = tmp_path / "skills" / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: demo\ndescription: example\n---\nSKILL_CONSTRAINT", encoding="utf-8")
    p = Scripted([ModelResponse(tool_calls=(ToolCall("load", "skill_load", {"name": "demo"}),)), ModelResponse(text="done"), ModelResponse(text="next")])
    rt = make_runtime(tmp_path / "state", p, skill_roots=[root.parent])
    session = rt.create_session("agent.fast")
    assert rt.start_turn(session.session_id, "use demo").status is AgentStatus.COMPLETED
    assert any("SKILL_CONSTRAINT" in str(m.content) for m in p.requests[-1].messages if m.name == "loom_active_skills")
    rt.start_turn(session.session_id, "next task")
    rt.compact_context(session.session_id, "earlier work summary", keep_recent=2)
    rt.close()
    next_platform = Scripted([ModelResponse(text="continued")])
    resumed = make_runtime(tmp_path / "state", next_platform, skill_roots=[root.parent])
    assert resumed.start_turn(session.session_id, "continue").status is AgentStatus.COMPLETED
    assert any("SKILL_CONSTRAINT" in str(m.content) for m in next_platform.requests[0].messages if m.name == "loom_active_skills")
    resumed.close()


def test_mxc_discovers_direct_venv_launcher_dependencies(tmp_path):
    venv = tmp_path / "venv"
    base = tmp_path / "python"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    base.mkdir()
    (scripts / "python.exe").touch()
    (base / "python.exe").touch()
    (venv / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
    paths = SandboxManager._windows_tool_read_paths(argv=(str(scripts / "python.exe"),), environment={})
    assert str(venv.resolve()) in paths
    assert str(base.resolve()) in paths


def test_generic_provider_eof_is_not_completion():
    from app.ai.streaming_platform import _StreamAccumulator
    from app.ai import StreamEvent, StreamEventKind
    from app.ai.errors import AITransportError
    stream = _StreamAccumulator()
    stream.consume(StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="partial"))
    with pytest.raises(AITransportError, match="completion marker"):
        stream.finalize()


def test_execution_lease_excludes_another_process_and_releases(tmp_path):
    import subprocess
    import sys
    from app.agent_runtime.journal import ExecutionLease
    script = "from pathlib import Path; from app.agent_runtime.journal import ExecutionLease; import sys\nwith ExecutionLease(Path(sys.argv[1])): print('acquired')"
    with ExecutionLease(tmp_path):
        blocked = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
        assert blocked.returncode != 0
        assert "another process" in blocked.stderr
    allowed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert allowed.returncode == 0
