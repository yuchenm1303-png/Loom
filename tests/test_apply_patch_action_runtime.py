from __future__ import annotations

import pytest

from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentStatus, PermissionMode
from app.agent_runtime.patch_tools import apply_patch_tool
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolRegistry


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _call(*, content: str = "hello\n", expected_text: str | None = None) -> ToolCall:
    change = {
        "action": "update" if expected_text is not None else "add",
        "path": "note.txt",
    }
    if expected_text is None:
        change["content"] = content
    else:
        change["content"] = content
        change["expected_text"] = expected_text
    return ToolCall(
        call_id="patch-1",
        name="apply_patch",
        arguments={"changes": [change]},
    )


def _runtime(tmp_path, call: ToolCall, *, final_text: str = "done") -> AgentRuntime:
    return AgentRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(tool_calls=(call,)),
                ModelResponse(text=final_text),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((apply_patch_tool(),)),
    )


def _session(runtime: AgentRuntime, tmp_path):
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )


def test_approved_unchanged_patch_executes_through_core_action_binding(tmp_path):
    call = _call(content="approved content\n")
    runtime = _runtime(tmp_path, call)
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Apply the patch.")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert not (tmp_path / "note.txt").exists()

        completed = runtime.resume_approval(
            session.session_id,
            call.call_id,
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved content\n"
    finally:
        runtime.close()


def test_queued_patch_argument_drift_is_rejected_before_file_write(tmp_path):
    call = _call(content="original\n")
    runtime = _runtime(tmp_path, call)
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Apply the patch.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        stored = runtime.store.load(session.session_id)
        stored.pending_tool_calls[0] = _call(content="changed after approval request\n")
        runtime.store.save(stored)

        with pytest.raises(ValueError, match="approval binding changed"):
            runtime.resume_approval(
                session.session_id,
                call.call_id,
                approved=True,
            )

        assert not (tmp_path / "note.txt").exists()
    finally:
        runtime.close()


def test_workspace_preimage_drift_is_left_to_patch_runtime_validation(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    call = _call(content="after\n", expected_text="before\n")
    runtime = _runtime(tmp_path, call, final_text="patch failure observed")
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Apply the guarded patch.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        target.write_text("external drift\n", encoding="utf-8")

        completed = runtime.resume_approval(
            session.session_id,
            call.call_id,
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "patch failure observed"
        assert target.read_text(encoding="utf-8") == "external drift\n"
    finally:
        runtime.close()
