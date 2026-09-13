from __future__ import annotations

import json
from dataclasses import replace

from app.ai import ToolCall
from app.agent_runtime.apply_patch_action import ApplyPatchActionIdentity
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.execution_action import execution_action_for
from app.agent_runtime.execution_binding import action_binding_digest, binding_digest
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


class BarePlatform:
    pass


def _tool() -> AgentTool:
    return AgentTool(
        name="apply_patch",
        description="Synthetic apply_patch tool for action identity tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.MUTATING,
    )


def _step(tmp_path) -> StepContext:
    tool = _tool()
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.APPROVAL,
        tool_router=ToolRouter((tool,)),
    )


def _structured_call(*, call_id="patch-1", path="note.txt", content="private-content") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name="apply_patch",
        arguments={
            "changes": [
                {
                    "action": "add",
                    "path": path,
                    "content": content,
                }
            ]
        },
    )


def test_structured_patch_identity_does_not_copy_content(tmp_path):
    action = ApplyPatchActionIdentity.build(
        _step(tmp_path),
        _structured_call(content="private-content-value"),
    )
    rendered = json.dumps(action.binding_payload(), sort_keys=True)

    assert action.kind == "apply_patch"
    assert action.paths == ("note.txt",)
    assert len(action.request_digest) == 64
    assert "private-content-value" not in rendered
    assert "call_id" not in action.binding_payload()


def test_structured_patch_canonicalizes_call_id_path_spelling_and_object_key_order(tmp_path):
    step = _step(tmp_path)
    first = _structured_call(call_id="patch-1", path="./note.txt", content="hello")
    second = ToolCall(
        call_id="patch-2",
        name="apply_patch",
        arguments={
            "changes": [
                {
                    "content": "hello",
                    "path": "note.txt",
                    "action": "ADD",
                }
            ]
        },
    )

    left = ApplyPatchActionIdentity.build(step, first)
    right = ApplyPatchActionIdentity.build(step, second)

    assert left.call_id != right.call_id
    assert left.paths == right.paths == ("note.txt",)
    assert left.binding_payload() == right.binding_payload()
    assert left.digest() == right.digest()


def test_structured_patch_content_change_changes_identity(tmp_path):
    step = _step(tmp_path)
    first = ApplyPatchActionIdentity.build(step, _structured_call(content="first"))
    second = ApplyPatchActionIdentity.build(step, _structured_call(content="second"))

    assert first.request_digest != second.request_digest
    assert first.digest() != second.digest()


def test_text_patch_normalizes_line_endings_and_extracts_paths(tmp_path):
    step = _step(tmp_path)
    text = "\n".join(
        (
            "*** Begin Patch",
            "*** Add File: ./folder/../note.txt",
            "+hello",
            "*** End Patch",
        )
    )
    left = ApplyPatchActionIdentity.build(
        step,
        ToolCall(call_id="patch-1", name="apply_patch", arguments={"patch": text}),
    )
    right = ApplyPatchActionIdentity.build(
        step,
        ToolCall(
            call_id="patch-2",
            name="apply_patch",
            arguments={"patch": text.replace("\n", "\r\n")},
        ),
    )

    assert left.paths == right.paths == ("note.txt",)
    assert left.digest() == right.digest()


def test_patch_identity_does_not_bind_live_workspace_preimage(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("before one\n", encoding="utf-8")
    step = _step(tmp_path)
    call = ToolCall(
        call_id="patch-1",
        name="apply_patch",
        arguments={
            "changes": [
                {
                    "action": "update",
                    "path": "note.txt",
                    "old_text": "before",
                    "new_text": "after",
                }
            ]
        },
    )

    first = ApplyPatchActionIdentity.build(step, call)
    target.write_text("before two\n", encoding="utf-8")
    second = ApplyPatchActionIdentity.build(step, call)

    assert first.binding_payload() == second.binding_payload()
    assert first.digest() == second.digest()


def test_generic_action_factory_and_binding_use_apply_patch_identity(tmp_path):
    step = _step(tmp_path)
    tool = step.tool_router.get("apply_patch")
    assert tool is not None
    call = _structured_call(content="hello")

    action = execution_action_for(step, call)
    assert isinstance(action, ApplyPatchActionIdentity)

    generic = binding_digest(step, tool, BarePlatform())
    bound = action_binding_digest(step, tool, call, BarePlatform())
    changed = action_binding_digest(
        step,
        tool,
        _structured_call(call_id="patch-2", content="different"),
        BarePlatform(),
    )

    assert bound != generic
    assert changed != bound
