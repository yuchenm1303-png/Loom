from __future__ import annotations

import sys

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    ProcessStore,
    SandboxManager,
    SandboxPolicy,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _runtime(tmp_path, responses):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = AgentRuntime(
        platform=ScriptedPlatform(responses),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        process_store=ProcessStore(
            sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        ),
    )
    session = runtime.create_session(
        "agent.fast",
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )
    return runtime, session, workspace


def test_exec_approval_executes_exact_pending_action_once(tmp_path):
    marker = "codex-parity-exec-marker"
    argv = [sys.executable, "-c", f"print({marker!r})"]
    runtime, session, _workspace = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-action",
                        name="exec",
                        arguments={"argv": argv, "timeout_seconds": 10},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="exec observed", finish_reason="stop"),
        ],
    )
    try:
        waiting = runtime.start_turn(session.session_id, "run the command")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.call_id == "exec-action"
        assert waiting.pending_approval.tool_name == "exec"
        assert waiting.pending_approval.arguments == {
            "argv": argv,
            "timeout_seconds": 10,
        }
        assert not any(
            event.kind is AgentEventKind.PROCESS_STARTED
            for event in runtime.store.events(session.session_id)
        )

        result = runtime.resume_approval(
            session.session_id,
            "exec-action",
            approved=True,
        )

        assert result.status is AgentStatus.COMPLETED
        completed = [
            event
            for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.TOOL_COMPLETED
            and event.data.get("call_id") == "exec-action"
        ]
        assert len(completed) == 1
        assert marker in completed[0].data["content"]
        started = [
            event
            for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.PROCESS_STARTED
        ]
        assert len(started) == 1
        assert started[0].data["argv"] == argv
    finally:
        runtime.close()


def test_apply_patch_approval_executes_exact_pending_patch_once(tmp_path):
    patch = (
        "*** Begin Patch\n"
        "*** Add File: approved.txt\n"
        "+approved payload\n"
        "*** End Patch"
    )
    runtime, session, workspace = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="patch-action",
                        name="apply_patch",
                        arguments={"patch": patch},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="patch observed", finish_reason="stop"),
        ],
    )
    try:
        waiting = runtime.start_turn(session.session_id, "apply the patch")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.call_id == "patch-action"
        assert waiting.pending_approval.tool_name == "apply_patch"
        assert waiting.pending_approval.arguments == {"patch": patch}
        assert not (workspace / "approved.txt").exists()

        result = runtime.resume_approval(
            session.session_id,
            "patch-action",
            approved=True,
        )

        assert result.status is AgentStatus.COMPLETED
        assert (workspace / "approved.txt").read_text(encoding="utf-8") == "approved payload\n"
        completed = [
            event
            for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.TOOL_COMPLETED
            and event.data.get("call_id") == "patch-action"
        ]
        assert len(completed) == 1
        diffs = [
            event
            for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.TURN_DIFF_UPDATED
        ]
        assert len(diffs) == 1
        assert "approved.txt" in diffs[0].data["paths"]
    finally:
        runtime.close()


def test_denied_exec_action_becomes_observation_without_side_effect(tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')",
    ]
    runtime, session, _workspace = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-denied",
                        name="exec",
                        arguments={"argv": argv},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="denial observed", finish_reason="stop"),
        ],
    )
    try:
        assert runtime.start_turn(session.session_id, "try it").status is AgentStatus.WAITING_APPROVAL

        result = runtime.resume_approval(
            session.session_id,
            "exec-denied",
            approved=False,
        )

        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "denial observed"
        assert not marker.exists()
        denied = [
            event
            for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.TOOL_DENIED
            and event.data.get("call_id") == "exec-denied"
        ]
        assert len(denied) == 1
        assert denied[0].data["source"] == "user"
        assert not any(
            event.kind is AgentEventKind.PROCESS_STARTED
            for event in runtime.store.events(session.session_id)
        )
    finally:
        runtime.close()
