"""Adversarial model responses: completion is attempted without any QA tools."""
import json
import subprocess

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentStatus, AgentEventKind as E
from app.agent_runtime.development_verification import source_kind, feature_recipes
from app.agent_runtime.tools import ToolContext
from test_development_feedback import make_runtime, image_bytes


def write(call="edit", path="src/Panel.tsx", text="export const Panel = () => <button>Fixed</button>"):
    return ModelResponse(tool_calls=(ToolCall(call, "write_workspace_text", {"path": path, "text": text}),))


def development(request):
    messages = [m for m in request.messages if m.name == "loom_development_verification"]
    return json.loads(messages[-1].content.split("\n")[-1]) if messages else None


class Responses:
    def __init__(self, values):
        self.values = iter(values)
        self.requests = []
    def execute_chat(self, profile, request):
        self.requests.append(request)
        return next(self.values)


def test_ui_edit_without_check_registration_creates_obligation_and_bounds_false_completion(tmp_path):
    platform = Responses([write(), ModelResponse(text="Fixed and deployed."),
                          ModelResponse(text="Everything is done."), ModelResponse(text="All good.")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    try:
        result = runtime.start_turn(session.session_id, "Fix the clipped panel")
        # Recovery stays bounded, but the work and the model's report are kept: the turn completes
        # and says what is unverified instead of being thrown away as a failed run.
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text.startswith("All good.")
        assert "1 source file(s) changed in this turn still lack current acceptance evidence" in result.final_text
        assert len(platform.requests) == 4
        assert development(platform.requests[1])["subjects"][0]["kind"] == "visual"
        assert development(platform.requests[1])["unresolved"] == 1
        events = runtime.store.events(session.session_id)
        assert not any(e.kind is E.LIMIT_REACHED for e in events)
        attempts = [e for e in events if e.kind is E.VERIFICATION_FINISH_REQUESTED]
        assert [e.data["attempt"] for e in attempts] == [1, 2, 3]
        assert attempts[-1].data["exhausted"] is True
        completed = next(e for e in events if e.kind is E.TURN_COMPLETED)
        assert completed.data["development_verification"]["unresolved"] == 1
        assert completed.data["verification_notice"]
        endings = [e for e in events if e.kind is E.MODEL_RESPONSE and not e.data.get("tool_calls")]
        assert [e.data["display_phase"] for e in endings] == ["commentary", "commentary", "final_answer"]
        assert result.usage.total_tokens >= 0
    finally:
        runtime.close()


def test_static_pass_cannot_discharge_ui_and_evidenced_blocker_can_finish(tmp_path):
    import sys
    platform = Responses([write(), ModelResponse(tool_calls=(ToolCall("static", "exec", {
        "argv": [sys.executable, "-c", "print('build passed')"], "verification": {
            "case_id": "build", "expectation": "Build exits", "subject_paths": ["src/Panel.tsx"]}}),)),
        ModelResponse(text="Done"), ModelResponse(tool_calls=(ToolCall("blocked", "resolve_verification", {
            "paths": ["src/Panel.tsx"], "disposition": "blocked",
            "reason": "No application launch recipe is available in this fixture; build coverage is insufficient.",
            "evidence_refs": [{"call_id": "static"}]}),)),
        ModelResponse(text="Source changed and built; UI acceptance remains blocked.")])
    runtime, session, _ = make_runtime(tmp_path, platform)
    try:
        result = runtime.start_turn(session.session_id, "Fix the UI")
        assert result.status is AgentStatus.COMPLETED
        assert development(platform.requests[2])["unresolved"] == 1
        terminal = next(e for e in reversed(runtime.store.events(session.session_id)) if e.kind is E.TURN_COMPLETED)
        assert terminal.data["development_verification"]["status"] == "blocked"
        assert "acceptance remains blocked" in terminal.data["text"]
        assert "acceptance remains blocked" in terminal.data.get("display_text", terminal.data["text"])
        assert "blocked" in result.final_text
    finally:
        runtime.close()


_STYLES = "".join(f".rule-{n} {{ margin: {n}px; }}\n" for n in range(40))


def _style_turn(tmp_path, old, new):
    import sys
    platform = Responses([
        ModelResponse(tool_calls=(ToolCall("edit", "replace_workspace_text", {
            "path": "src/panel.css", "old_text": old, "new_text": new}),)),
        ModelResponse(tool_calls=(ToolCall("build", "exec", {
            "argv": [sys.executable, "-c", "print('build passed')"], "verification": {
                "case_id": "build", "expectation": "Build exits 0", "subject_paths": ["src/panel.css"]}}),)),
        ModelResponse(text="Done"), ModelResponse(text="Still done"), ModelResponse(text="Done again")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "src" / "panel.css").write_text(_STYLES, encoding="utf-8")
    return platform, runtime, session


def test_small_style_edit_is_light_and_a_build_check_discharges_it(tmp_path):
    # Field case (2026-10-29): two CSS values took 70 steps because only screenshots could close them.
    platform, runtime, session = _style_turn(tmp_path, ".rule-3 { margin: 3px; }", ".rule-3 { margin: 4px; }")
    try:
        result = runtime.start_turn(session.session_id, "Nudge rule 3 down a pixel")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Done"
        assert len(platform.requests) == 3
        subject = development(platform.requests[1])["subjects"][0]
        assert subject["kind"] == "visual" and subject["light"] is True
        assert any("marks such subjects light" in str(m.content) for m in platform.requests[1].messages)
        assert development(platform.requests[2])["status"] == "passed"
        events = runtime.store.events(session.session_id)
        assert not any(e.kind is E.VERIFICATION_FINISH_REQUESTED for e in events)
    finally:
        runtime.close()


def test_large_style_edit_still_needs_visual_evidence(tmp_path):
    old = "".join(f".rule-{n} {{ margin: {n}px; }}\n" for n in range(20))
    new = "".join(f".rule-{n} {{ margin: {n + 1}px; }}\n" for n in range(20))
    platform, runtime, session = _style_turn(tmp_path, old, new)
    try:
        runtime.start_turn(session.session_id, "Rework every rule")
        subject = development(platform.requests[2])["subjects"][0]
        assert "light" not in subject
        assert development(platform.requests[2])["unresolved"] == 1
        events = runtime.store.events(session.session_id)
        assert any(e.kind is E.VERIFICATION_FINISH_REQUESTED for e in events)
    finally:
        runtime.close()


def test_visual_pass_rejects_files_only_and_unseen_images(tmp_path):
    runtime, session, workspace = make_runtime(tmp_path, object())
    (workspace / "Panel.tsx").write_text("export {}")
    (workspace / "frame.png").write_bytes(image_bytes())
    session.current_turn_id = "turn"
    runtime.store.save(session)
    runtime.development_verification.begin(session)
    try:
        context = ToolContext(session.session_id, session.current_turn_id, workspace,
            services={"development_verification": runtime.development_verification},
            emit_event=lambda kind, data: runtime._emit_event(session, kind, data=data))
        result = runtime.tools.get("record_check").handler(context, {"case_id": "looks-good", "check_kind": "visual",
            "expectation": "Panel is visible", "observed": "Pretended to inspect it", "verdict": "passed",
            "subject_paths": ["Panel.tsx"], "evidence_refs": [{"path": "frame.png"}]})
        assert not result.ok
        assert "delivered to the model" in result.content
    finally:
        runtime.close()


def test_workspace_image_captured_before_edit_is_not_fresh_visual_evidence(tmp_path):
    from app.agent_runtime.tools import ToolResult
    runtime, session, workspace = make_runtime(tmp_path, object())
    session.current_turn_id = "turn"
    runtime.store.save(session)
    (workspace / "old.png").write_bytes(image_bytes())
    runtime.development_verification.begin(session)
    (workspace / "Panel.tsx").write_text("export {}")
    tracker = runtime.diff_trackers.for_turn(session.session_id, session.current_turn_id)
    tracker.record_text_change("Panel.tsx", before=None, after="export {}")
    runtime.development_verification.observe(session)
    context = ToolContext(session.session_id, session.current_turn_id, workspace)
    try:
        image_result = runtime.tools.get("view_images").handler(context, {"images": [{"path": "old.png"}]})
        runtime.development_verification.observed(session, ToolCall("image", "view_images", {}), image_result)
        runtime.development_verification.observed(session, ToolCall("action", "exec", {}), ToolResult(True, "action"))
        runtime.development_verification.delivered(session, {"workspace_visual_observation": {"source_call_ids": ["image"]}})
        runtime.development_verification.confirm_delivery(session)
        assert not runtime.development_verification.visual_evidence(session, [{"call_id": "image"}, {"call_id": "action"}])
    finally:
        runtime.close()


def test_pending_obligations_survive_reload_and_followup(tmp_path):
    platform = Responses([write(), ModelResponse(text="Done"), ModelResponse(text="Done"), ModelResponse(text="Done")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    try:
        runtime.start_turn(session.session_id, "Fix the UI")
        first_state = runtime.development_verification.latest(session)
        assert "src/Panel.tsx" in first_state["subjects"]
        runtime.store.save(session)
        session = runtime.store.load(session.session_id)
        session.current_turn_id = "followup"
        runtime.store.save(session)
        runtime.development_verification.begin(session)
        state = runtime.development_verification.summary(session)
        assert state["unresolved"] == 1
        assert state["subjects"][0]["path"] == "src/Panel.tsx"
    finally:
        runtime.close()


def test_shell_edits_of_already_dirty_source_are_observed(tmp_path):
    from app.agent_runtime.development_verification import DevelopmentVerification
    import sys
    platform = Responses([ModelResponse(tool_calls=(ToolCall("shell-edit", "exec", {
        "argv": [sys.executable, "-c", "from pathlib import Path; Path('Panel.tsx').write_text('new dirty bytes')"]}),)),
        ModelResponse(text="Done"), ModelResponse(text="Done"), ModelResponse(text="Done")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "Panel.tsx").write_text("already dirty")
    try:
        result = runtime.start_turn(session.session_id, "Fix the panel through a script")
        assert result.status is AgentStatus.COMPLETED
        assert development(platform.requests[1])["subjects"][0]["path"] == "Panel.tsx"
    finally:
        runtime.close()


def test_edits_committed_inside_one_command_are_still_observed(tmp_path):
    import sys
    platform = Responses([ModelResponse(tool_calls=(ToolCall("edit-and-commit", "exec", {
        "argv": [sys.executable, "-c", "from pathlib import Path; import subprocess; "
                 "Path('Panel.tsx').write_text('new committed bytes'); subprocess.run(['git','add','Panel.tsx'],check=True); "
                 "subprocess.run(['git','commit','-qm','changed'],check=True)"]}),)),
        ModelResponse(text="Done"), ModelResponse(text="Done"), ModelResponse(text="Done")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    for key, value in (("user.name", "Verification Test"), ("user.email", "verification@example.test")):
        subprocess.run(["git", "-C", str(workspace), "config", key, value], check=True)
    (workspace / "Panel.tsx").write_text("original")
    subprocess.run(["git", "-C", str(workspace), "add", "Panel.tsx"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "baseline"], check=True)
    try:
        result = runtime.start_turn(session.session_id, "Fix the UI")
        assert result.status is AgentStatus.COMPLETED
        assert development(platform.requests[1])["subjects"][0]["path"] == "Panel.tsx"
    finally:
        runtime.close()


def test_nested_git_workspace_paths_are_relative_and_other_projects_are_excluded(tmp_path):
    from app.agent_runtime.development_verification import git_sources
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    project = tmp_path / "frontend"
    project.mkdir()
    (project / "Panel.tsx").write_text("changed")
    (tmp_path / "Other.tsx").write_text("unrelated")
    files, status = git_sources(project)
    assert status == "available"
    assert set(files) == {"Panel.tsx"}


def test_unrelated_followup_can_report_prior_pending_without_forced_execution(tmp_path):
    platform = Responses([write(), ModelResponse(text="Done"), ModelResponse(text="Done"), ModelResponse(text="Done"),
                          ModelResponse(text="The earlier UI change remains unverified.")])
    runtime, session, _ = make_runtime(tmp_path, platform)
    try:
        runtime.start_turn(session.session_id, "Fix the UI")
        result = runtime.start_turn(session.session_id, "Explain the current status without doing more work")
        assert result.status is AgentStatus.COMPLETED
        assert "remain unverified" in result.final_text
        assert runtime.development_verification.summary(runtime.store.load(session.session_id))["unresolved"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("path,kind", [("src/Panel.tsx", "visual"), ("src/styles/base.css", "visual"),
    ("app/service.py", "functional"), ("src/components/cursor.js", "visual"),
    ("tests/panel.test.tsx", None), (".loom/scratch/probe.py", None), ("README.md", None)])
def test_source_classification_has_explicit_non_ui_paths(path, kind):
    assert source_kind(path) == kind


def test_feature_map_is_advisory_bounded_and_reports_unmapped_source(tmp_path):
    context = ToolContext("s", "t", tmp_path)
    config = tmp_path / ".loom" / "verification.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"version": 1, "features": [{"id": "panel", "sources": ["src/*.tsx"],
        "entry": "http://localhost:5199", "exercise": "Close the panel", "expectation": "Controls remain visible"}]}))
    recipes, status = feature_recipes(context, ["src/Panel.tsx"])
    assert status == "configured" and recipes[0]["id"] == "panel"
    assert feature_recipes(context, ["app/backend.py"])[0] == []
    config.write_text("broken json")
    assert feature_recipes(context, ["src/Panel.tsx"])[1].startswith("invalid")


def test_changing_expected_exit_status_marks_a_check_for_review(tmp_path):
    import sys
    def command(call_id, expected):
        return ModelResponse(tool_calls=(ToolCall(call_id, "exec", {
            "argv": [sys.executable, "-c", "import sys; sys.exit(1)"], "verification": {
                "case_id": "same-check", "expectation": "Command acceptance", "subject_paths": ["source.txt"],
                "expected_exit_code": expected}}),))
    platform = Responses([command("fail", 0), command("weaken", 1), command("repeat-weakened", 1), ModelResponse(text="Recorded")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    (workspace / "source.txt").write_text("unchanged")
    try:
        runtime.start_turn(session.session_id, "Run the acceptance check")
        checks = [e.data for e in runtime.store.events(session.session_id) if e.kind is E.CHECK_RECORDED]
        assert checks[-1]["definition_changed"] is True
        assert checks[-1]["acceptance_evidence"] is False
        assert checks[-2]["definition_changed"] is True
        state = runtime._verification_summary(runtime.store.load(session.session_id))
        assert state["checks"][-1]["status"] == "needs_review"
        # The model learns it at once, in the tool result, not after it reports a verdict.
        results = {e.data["call_id"]: e.data["content"] for e in runtime.store.events(session.session_id)
                   if e.kind in (E.TOOL_COMPLETED, E.TOOL_FAILED)}
        assert "Not acceptance evidence" not in results["fail"]
        assert "Not acceptance evidence: this check for same-check" in results["weaken"]
        assert "Not acceptance evidence" in str(platform.requests[2].messages)
    finally:
        runtime.close()


def test_cancel_during_verification_recovery_ends_without_more_model_calls(tmp_path):
    class Platform(Responses):
        def execute_chat(self, profile, request):
            if self.requests:
                self.requests.append(request)
                runtime.cancel(session.session_id)
                return ModelResponse(text="must not be delivered")
            return super().execute_chat(profile, request)
    platform = Platform([write()])
    runtime, session, _ = make_runtime(tmp_path, platform)
    try:
        result = runtime.start_turn(session.session_id, "Fix the panel")
        assert result.status is AgentStatus.CANCELLED
        assert len(platform.requests) == 2
    finally:
        runtime.close()
