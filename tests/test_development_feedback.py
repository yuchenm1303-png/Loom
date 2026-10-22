"""Exercise the development loop through the real runtime and real subprocesses."""
from __future__ import annotations

import json
import sys
from io import BytesIO

import pytest

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.contracts import AgentEventKind as E, AgentStatus
from app.agent_runtime.tools import ToolContext, ToolResult, validate_tool_arguments
from app.agent_runtime.verification import subject_snapshot, verification_summary
from app.agent_runtime.visual_feedback import VisualFeedback, view_images_tool
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ImagePart, ModelResponse, TextPart, ToolCall


def image_bytes(color="red"):
    from PIL import Image
    output = BytesIO()
    Image.new("RGB", (320, 240), color).save(output, format="PNG")
    return output.getvalue()


def make_runtime(tmp_path, platform):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
                           auto_configure_browser=False, auto_configure_computer=False,
                           auto_configure_web_search=False, mcp_servers=())
    session = runtime.create_session("test", workspace_dir=workspace, permission_mode="full-access")
    return runtime, session, workspace


def check_call(call_id, expected=0):
    return ToolCall(call_id, "exec", {"argv": [sys.executable, "-c",
        "from pathlib import Path; import sys; sys.exit(0 if Path('source.txt').read_text() == 'fixed' else 1)"],
        "verification": {"case_id": "behavior", "expectation": "Source behavior returns the expected exit status",
                         "subject_paths": ["source.txt"], "expected_exit_code": expected}})


def test_real_failed_command_repair_retest_and_stale_pass_reach_model(tmp_path):
    class RepairPlatform:
        def __init__(self):
            self.step = 0
            self.states = []

        def execute_chat(self, profile, request):
            states = [m for m in request.messages if m.name == "loom_verification_state"]
            if states:
                self.states.append(json.loads(states[-1].content.split("\n")[-1])["status"])
            responses = [
                ModelResponse(tool_calls=(check_call("bad"),)),
                ModelResponse(tool_calls=(ToolCall("fix", "write_workspace_text", {"path": "source.txt", "text": "fixed"}),)),
                ModelResponse(tool_calls=(check_call("good"),)),
                ModelResponse(tool_calls=(ToolCall("edit-again", "write_workspace_text", {"path": "source.txt", "text": "changed"}),)),
                ModelResponse(text="Changed again; the previous check must be rerun."),
            ]
            response = responses[self.step]
            self.step += 1
            return response

    platform = RepairPlatform()
    runtime, session, workspace = make_runtime(tmp_path, platform)
    (workspace / "source.txt").write_text("broken")
    try:
        result = runtime.start_turn(session.session_id, "Fix and validate the behavior")
        assert result.status is AgentStatus.COMPLETED
        assert platform.states == ["failed", "stale", "passed", "stale"]
        events = runtime.store.events(session.session_id)
        checks = [e for e in events if e.kind is E.CHECK_RECORDED]
        assert [e.data["verdict"] for e in checks] == ["failed", "passed"]
        assert checks[-1].data["evidence_refs"] == [{"call_id": "good"}]
        terminal = next(e for e in reversed(events) if e.kind is E.TURN_COMPLETED)
        assert terminal.data["verification"]["status"] == "stale"
        assert terminal.data["task_completion"] == "not_assessed"
        # Freshness is recomputed after reloading a store, not remembered in RAM.
        reopened = FileAgentSessionStore(tmp_path / "state")
        context = ToolContext(session.session_id, result.turn_id, workspace)
        assert verification_summary(reopened.events(session.session_id), context)["status"] == "stale"
        projected = reopened.context_select(session.session_id, kinds=(E.CHECK_RECORDED,))
        assert projected[-1].data["subject_snapshot"] == checks[-1].data["subject_snapshot"]
    finally:
        runtime.close()


@pytest.mark.parametrize("script,expected,verdict", [
    ("import sys; sys.exit(3)", 3, "passed"),
    ("import sys; sys.exit(3)", 0, "failed"),
    ("from pathlib import Path; Path('source.txt').write_text('rewritten')", 0, "stale"),
])
def test_command_assessment_uses_real_exit_code_and_boundary_source(tmp_path, script, expected, verdict):
    class Platform:
        def __init__(self):
            self.first = True
        def execute_chat(self, profile, request):
            if self.first:
                self.first = False
                return ModelResponse(tool_calls=(ToolCall("check", "exec", {
                    "argv": [sys.executable, "-c", script], "verification": {
                        "case_id": "exit", "expectation": "Declared exit status", "subject_paths": ["source.txt"],
                        "expected_exit_code": expected}}),))
            return ModelResponse(text="Results recorded")
    runtime, session, workspace = make_runtime(tmp_path, Platform())
    (workspace / "source.txt").write_text("original")
    try:
        runtime.start_turn(session.session_id, "Run the check")
        checks = [e for e in runtime.store.events(session.session_id) if e.kind is E.CHECK_RECORDED]
        assert checks[-1].data["verdict"] == verdict
        assert checks[-1].data["assessment_source"] == "runtime_exit_code"
    finally:
        runtime.close()


def test_unstartable_verification_command_is_recorded_as_blocked(tmp_path):
    class Platform:
        def __init__(self):
            self.first = True
        def execute_chat(self, profile, request):
            if self.first:
                self.first = False
                return ModelResponse(tool_calls=(ToolCall("missing", "exec", {"argv": ["loom-nonexistent-executable-93847"],
                    "verification": {"case_id": "launch", "expectation": "Executable runs", "subject_paths": ["source.txt"]}}),))
            state = next(m for m in reversed(request.messages) if m.name == "loom_verification_state")
            assert '"status": "blocked"' in state.content
            return ModelResponse(text="Check blocked: executable missing.")
    runtime, session, _ = make_runtime(tmp_path, Platform())
    try:
        runtime.start_turn(session.session_id, "Validate")
    finally:
        runtime.close()


def test_scope_catches_additions_deletions_and_same_size_content_changes(tmp_path):
    context = ToolContext("s", "t", tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    file = source / "a.txt"
    file.write_text("one")
    before = subject_snapshot(context, ["src"])
    file.write_text("two")
    assert subject_snapshot(context, ["src"]) != before
    file.write_text("one")
    assert subject_snapshot(context, ["src"]) == before
    (source / "b.txt").write_text("new")
    assert subject_snapshot(context, ["src"]) != before
    (source / "b.txt").unlink()
    file.unlink()
    assert subject_snapshot(context, ["src"]) != before
    with pytest.raises(ValueError, match="escapes"):
        subject_snapshot(context, ["../outside"])


def test_scoped_visual_check_goes_stale_without_rewriting_ledger(tmp_path):
    runtime, session, workspace = make_runtime(tmp_path, object())
    (workspace / "source.css").write_text("before")
    (workspace / "evidence.png").write_bytes(image_bytes())
    session.current_turn_id = "visual-turn"
    try:
        def emit(kind, data):
            runtime._emit_event(session, kind, data=data)
        context = ToolContext(session.session_id, session.current_turn_id, workspace, emit_event=emit)
        result = runtime.tools.get("record_check").handler(context, {
            "case_id": "visual", "expectation": "Control stays visible", "observed": "Inspected captured frame",
            "verdict": "passed", "evidence_refs": [{"path": "evidence.png"}], "subject_paths": ["source.css"]})
        assert result.ok
        (workspace / "source.css").write_text("after")
        ledger = runtime.tools.get("read_check_ledger").handler(context, {}).data["entries"]
        assert ledger[-1]["freshness"] == "stale"
        assert ledger[-1]["verdict"] == "passed"  # historical assessment stays intact
    finally:
        runtime.close()


def test_workspace_images_reach_real_model_request_and_stay_out_of_persistence(tmp_path):
    class Platform:
        def __init__(self):
            self.step = 0
        def execute_chat(self, profile, request):
            self.step += 1
            if self.step == 1:
                return ModelResponse(tool_calls=(ToolCall("inspect", "view_images", {"images": [
                    {"path": "frame.png", "label": "frame 120 ms", "crop": {"x": 10, "y": 10, "width": 80, "height": 50}}]}),))
            attachments = [m for m in request.messages if m.name == "loom_tool_observation"]
            assert any(isinstance(p, ImagePart) for m in attachments for p in m.content)
            texts = [p.text for m in request.messages if isinstance(m.content, tuple) for p in m.content if isinstance(p, TextPart)]
            assert any('"attached_size": [80, 50]' in text for text in texts)
            return ModelResponse(text="Image inspected")
    runtime, session, workspace = make_runtime(tmp_path, Platform())
    (workspace / "frame.png").write_bytes(image_bytes())
    try:
        runtime.start_turn(session.session_id, "Inspect the generated capture")
        persisted = (runtime.store.session_dir(session.session_id) / "session.json").read_text(encoding="utf-8")
        assert "data:image" not in persisted
        event_text = "\n".join(json.dumps(e.data) for e in runtime.store.events(session.session_id))
        assert "data:image" not in event_text
        assert not runtime.visual_feedback._staged and not runtime.visual_feedback._ready
    finally:
        runtime.close()


def test_image_validation_is_atomic_and_confined(tmp_path):
    feedback = VisualFeedback()
    tool = view_images_tool(feedback)
    context = ToolContext("s", "t", tmp_path)
    (tmp_path / "ok.png").write_bytes(image_bytes())
    (tmp_path / "bad.png").write_text("not an image")
    with pytest.raises(ValueError, match="escapes"):
        tool.handler(context, {"images": [{"path": "../outside.png"}]})
    with pytest.raises(Exception):
        tool.handler(context, {"images": [{"path": "ok.png"}, {"path": "bad.png"}]})
    assert not feedback._staged
    with pytest.raises(ValueError, match="crop"):
        tool.handler(context, {"images": [{"path": "ok.png", "crop": {"x": 300, "y": 0, "width": 100, "height": 20}}]})
    with pytest.raises(Exception):
        validate_tool_arguments(tool.input_schema, {"images": []})


def test_visual_feedback_is_once_only_and_isolated_by_session(tmp_path):
    from types import SimpleNamespace
    feedback = VisualFeedback()
    (tmp_path / "frame.png").write_bytes(image_bytes())
    data = feedback.stage(ToolContext("owner", "turn", tmp_path), [{"path": "frame.png"}])
    owner = SimpleNamespace(session_id="owner", current_turn_id="turn")
    stranger = SimpleNamespace(session_id="stranger", current_turn_id="turn")
    assert feedback.collect(stranger) == ([], {})
    feedback.adopt(owner, ToolCall("image", "view_images", {}), ToolResult(True, "observed", data))
    assert feedback.collect(owner)[1]["workspace_visual_observation"]["image_count"] == 1
    assert feedback.collect(owner) == ([], {})


def test_scopes_reject_external_symlinks_and_overbroad_sources(tmp_path):
    context = ToolContext("s", "t", tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "secret.txt").write_text("must not be read")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    with pytest.raises(ValueError, match="escapes"):
        subject_snapshot(context, ["link"])


def test_truncated_verification_preview_never_reports_all_passed(tmp_path):
    from app.agent_runtime.contracts import AgentEvent
    events = [AgentEvent(str(i), "s", "t", E.CHECK_RECORDED, "2026-10-22T00:00:00Z",
                         {"case_id": str(i), "verdict": "failed" if i == 0 else "passed"}) for i in range(25)]
    summary = verification_summary(events, ToolContext("s", "t", tmp_path))
    assert summary["status"] == "incomplete"
    assert summary["omitted"] == 1


def test_parallel_verifications_each_have_durable_executed_evidence(tmp_path):
    class Platform:
        def __init__(self):
            self.first = True
        def execute_chat(self, profile, request):
            if self.first:
                self.first = False
                return ModelResponse(tool_calls=tuple(ToolCall(f"parallel-{i}", "exec", {
                    "argv": [sys.executable, "-c", "print('checked')"], "verification": {
                        "case_id": f"case-{i}", "expectation": "Command exits", "subject_paths": ["source.txt"]}})
                    for i in range(2)))
            state = next(m for m in reversed(request.messages) if m.name == "loom_verification_state")
            assert json.loads(state.content.split("\n")[-1])["total"] == 2
            return ModelResponse(text="Both checks ran")
    runtime, session, workspace = make_runtime(tmp_path, Platform())
    (workspace / "source.txt").write_text("source")
    try:
        runtime.start_turn(session.session_id, "Check both")
        checks = [e for e in runtime.store.events(session.session_id) if e.kind is E.CHECK_RECORDED]
        assert len(checks) == 2
        assert all(e.data["evidence_facts"][0]["result_ok"] for e in checks)
        assert {e.data["evidence_refs"][0]["call_id"] for e in checks} == {"parallel-0", "parallel-1"}
    finally:
        runtime.close()
