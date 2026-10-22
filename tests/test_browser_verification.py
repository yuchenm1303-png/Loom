from dataclasses import replace

import pytest

from app.ai import ToolCall
from app.agent_runtime.contracts import AgentEventKind as E
from app.agent_runtime.tools import ToolContext
from test_browser_capture_sequence import capture_runtime


def recipe(opened):
    return {"browser_id": opened.data["browser_id"], "state_revision": opened.data["state_revision"],
        "verification": {"case_id": "panel", "expectation": "Toggle changes the button", "subject_paths": ["Panel.tsx"]},
        "steps": [{"type": "action", "tool": "browser_click", "arguments": {"index": 1}},
                  {"type": "assert", "label": "Button updated", "expression": "true"}]}


def prepare(capture_runtime, monkeypatch, value=True):
    runtime, session, context, opened = capture_runtime
    (context.workspace / "Panel.tsx").write_text("export {}")
    runtime.store.save(session)
    runtime.development_verification.begin(session)
    tracker = runtime.diff_trackers.for_turn(session.session_id, session.current_turn_id)
    tracker.record_text_change("Panel.tsx", before=None, after="export {}")
    runtime.development_verification.observe(session)
    step = runtime._build_step_context(session, next_model_step=True)
    context = replace(context, services={"execution_step": step},
                      emit_event=lambda kind, data: runtime._emit_event(session, kind, data=data))
    backend = runtime.browser_sessions._owned(session.session_id, opened.data["browser_id"]).backend
    monkeypatch.setattr(backend, "evaluate", lambda *_args, **_kw: {"ok": True, "value": value}, raising=False)
    return runtime, session, context, opened, backend


def test_replay_native_actions_strict_assertion_and_model_delivery(capture_runtime, monkeypatch):
    runtime, session, context, opened, backend = prepare(capture_runtime, monkeypatch)
    args = recipe(opened)
    result = runtime.tools.get("browser_run_check").handler(context, args)
    assert result.ok, result.content
    assert backend.clicked == 1
    call = ToolCall("check", "browser_run_check", args)
    runtime._emit_event(session, E.TOOL_STARTED, data={"tool": call.name, "call_id": call.call_id})
    runtime._append_tool_result(session, call, result, failed=False)
    assert runtime.development_verification.summary(session)["unresolved"] == 1
    step = runtime._build_step_context(session, next_model_step=True)
    runtime._prepare_model_request(session, step, None)
    assert runtime.development_verification.summary(session)["unresolved"] == 1
    assert runtime.development_verification.before_finish(session) is not None
    assert runtime.development_verification.before_finish(session, accepting_response=True) is None
    assert runtime.development_verification.summary(session)["unresolved"] == 1
    runtime._emit_event(session, E.MODEL_RESPONSE, data={"text": "Inspected the screenshot"})
    assert runtime.development_verification.summary(session)["unresolved"] == 0
    # Subsequent edits invalidate both source and visual/action receipts.
    (context.workspace / "Panel.tsx").write_text("export const Changed = true")
    tracker = runtime.diff_trackers.for_turn(session.session_id, session.current_turn_id)
    tracker.record_text_change("Panel.tsx", before="export {}", after="export const Changed = true")
    runtime.development_verification.observe(session)
    assert runtime.development_verification.summary(session)["unresolved"] == 1


@pytest.mark.parametrize("value", [False, "true", 1, None, {"passed": True}])
def test_truthy_or_false_values_do_not_pass_assertions(capture_runtime, monkeypatch, value):
    runtime, _, context, opened, _ = prepare(capture_runtime, monkeypatch, value)
    result = runtime.tools.get("browser_run_check").handler(context, recipe(opened))
    assert not result.ok
    assert result.data["verification"]["verdict"] == "failed"
    assert result.data["images"]  # failed evidence is still inspectable


def test_recipe_cannot_switch_browser_or_run_unapproved_tools(capture_runtime, monkeypatch):
    runtime, _, context, opened, backend = prepare(capture_runtime, monkeypatch)
    args = recipe(opened)
    args["steps"][0]["arguments"]["browser_id"] = "another-browser"
    with pytest.raises(ValueError, match="owned by the recipe"):
        runtime.tools.get("browser_run_check").handler(context, args)
    args = recipe(opened)
    args["steps"][1] = {"type": "action", "tool": "exec", "arguments": {"argv": ["bad"]}}
    with pytest.raises(ValueError, match="unsupported"):
        runtime.tools.get("browser_run_check").handler(context, args)
    assert backend.clicked == 0


def test_read_only_recipe_never_injects_action(capture_runtime, monkeypatch):
    from app.agent_runtime.contracts import PermissionMode
    runtime, session, context, opened, backend = prepare(capture_runtime, monkeypatch)
    runtime.set_permission_mode(session.session_id, PermissionMode.READ_ONLY)
    session = runtime.store.load(session.session_id)
    step = runtime._build_step_context(session, next_model_step=True)
    context = replace(context, permission_mode="read-only", services={"execution_step": step})
    # Permission transitions close live browser handles. Either ownership or
    # central policy must refuse before an OS/browser input is injected.
    try:
        result = runtime.tools.get("browser_run_check").handler(context, recipe(opened))
        assert not result.ok
    except Exception:
        pass
    assert backend.clicked == 0
