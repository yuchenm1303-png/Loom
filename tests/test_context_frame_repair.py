from types import SimpleNamespace
from app.ai import AIMessage, MessageRole as R, ToolCall
from app.agent_runtime.context_composer import capture_context, render_request
from app.agent_runtime.history import repair_tool_history
import json


def capture(history):
    session = SimpleNamespace(session_id="session", request_context_frames=[], messages=list(history), workspace_dir=".")
    runtime = SimpleNamespace(_request_context_messages=lambda *_: (), _context_envelope=lambda *_: None,
        _request_context_provider_messages=lambda *_: (), instruction_loader=SimpleNamespace(load=lambda _: ""),
        _execution_context=lambda _: ([AIMessage(role=R.USER, name="loom_runtime_state", content="snapshot")], {}),
        _collect_model_observations=lambda *_: ([], {}), store=SimpleNamespace(save=lambda _: None))
    step = SimpleNamespace(step_id="step", request_state=SimpleNamespace(captured=False))
    capture_context(runtime, session, step)
    return runtime, session


def test_repair_insertions_do_not_move_frame_ahead_of_captured_user():
    user = AIMessage(role=R.USER, content="continue")
    history = [AIMessage(role=R.ASSISTANT, content="", tool_calls=(ToolCall("call", "probe", {}),)), user]
    runtime, session = capture(history)
    repaired = repair_tool_history(history).messages
    rendered = render_request(runtime, session, [], repaired)
    assert rendered.index(user) < next(i for i, m in enumerate(rendered) if m.name == "loom_runtime_state")


def test_frame_never_splits_recovered_tool_protocol():
    history = [AIMessage(role=R.USER, content="check"),
        AIMessage(role=R.ASSISTANT, content="", tool_calls=(ToolCall("call", "probe", {}),))]
    runtime, session = capture(history)
    repaired = repair_tool_history(history).messages
    rendered = render_request(runtime, session, [], repaired)
    assert rendered[1].tool_calls
    assert rendered[2].role is R.TOOL
    assert rendered[3].name == "loom_runtime_state"


def test_removing_orphan_does_not_move_old_frame_after_new_answer():
    history = [AIMessage(role=R.TOOL, tool_call_id="missing", content="orphan"),
        AIMessage(role=R.USER, content="request")]
    runtime, session = capture(history)
    repaired = list(repair_tool_history(history).messages)
    answer = AIMessage(role=R.ASSISTANT, content="answer")
    rendered = render_request(runtime, session, [], [*repaired, answer])
    assert next(i for i, m in enumerate(rendered) if m.name == "loom_runtime_state") < rendered.index(answer)


def test_anchor_roundtrip_survives_host_restart_and_changed_result_preview():
    history = [AIMessage(role=R.ASSISTANT, content="", tool_calls=(ToolCall("call", "probe", {}),)),
        AIMessage(role=R.TOOL, tool_call_id="call", content="full result")]
    runtime, session = capture(history)
    cold = SimpleNamespace(session_id=session.session_id,
        request_context_frames=json.loads(json.dumps(session.request_context_frames)))
    projected = [history[0], AIMessage(role=R.TOOL, tool_call_id="call", content="short preview"),
                 AIMessage(role=R.ASSISTANT, content="new answer")]
    rendered = render_request(SimpleNamespace(), cold, [], projected)
    assert rendered[2].name == "loom_runtime_state"
    assert rendered[3].content == "new answer"


def test_duplicate_plain_messages_keep_captured_occurrence_before_new_reply():
    repeated = AIMessage(role=R.USER, content="continue")
    runtime, session = capture([repeated, repeated])
    answer = AIMessage(role=R.ASSISTANT, content="answer")
    rendered = render_request(runtime, session, [], [repeated, repeated, answer])
    assert rendered[2].name == "loom_runtime_state"
    assert rendered[3] == answer


def test_checkpoint_resets_anchors_for_summary_and_later_appends():
    from app.agent_runtime.context_composer import compact_frames
    runtime, session = capture([AIMessage(role=R.USER, content="old request")])
    compact_frames(runtime, session, 1)
    summary = AIMessage(role=R.USER, name="loom_compaction", content="summary")
    answer = AIMessage(role=R.ASSISTANT, content="answer")
    session.messages = [summary, answer]
    rendered = render_request(runtime, session, [], session.messages)
    assert rendered[0] == summary
    assert rendered[1].name == "loom_runtime_state"
    assert rendered[2] == answer
