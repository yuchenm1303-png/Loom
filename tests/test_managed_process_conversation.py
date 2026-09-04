from __future__ import annotations

import json
import sys

from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, PermissionMode
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ToolCall


class InteractiveProcessPlatform:
    def __init__(self) -> None:
        self.stage = 0
        self.process_id = ""
        self.polls = 0

    def execute_chat(self, _profile_id, request):
        tool_messages = [message for message in request.messages if message.role is MessageRole.TOOL]
        if self.stage == 0:
            self.stage = 1
            script = "import sys; line=sys.stdin.readline(); print('echo:' + line.strip(), flush=True)"
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="bg-start",
                        name="start_workspace_command",
                        arguments={
                            "argv": [sys.executable, "-u", "-c", script],
                            "timeout_seconds": 15,
                        },
                    ),
                )
            )

        payload = json.loads(tool_messages[-1].content)
        data = payload.get("data") or {}
        if not self.process_id:
            self.process_id = str(data["process_id"])

        if self.stage == 1:
            self.stage = 2
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="bg-write",
                        name="write_workspace_process",
                        arguments={"process_id": self.process_id, "text": "hello Loom\n"},
                    ),
                )
            )

        if self.stage == 2:
            running = bool(data.get("running", True))
            stdout = str(data.get("stdout") or "")
            if running and self.polls < 20:
                self.polls += 1
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id=f"bg-poll-{self.polls}",
                            name="poll_workspace_process",
                            arguments={"process_id": self.process_id},
                        ),
                    )
                )
            assert running is False
            assert "echo:hello Loom" in stdout
            self.stage = 3
            return ModelResponse(text="background process interaction completed")

        raise AssertionError("unexpected model request")


def test_agent_can_start_write_and_poll_background_process_across_steps(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = InteractiveProcessPlatform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.FULL_ACCESS,
    )

    result = runtime.start_turn(session.session_id, "Run the interactive process.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "background process interaction completed"
    assert platform.process_id.startswith("proc-")
    assert platform.polls <= 20


def test_process_store_rejects_cross_session_control(tmp_path):
    runtime = AgentRuntime(
        platform=InteractiveProcessPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    process = runtime.process_store.start(
        session_id="owner-session",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=30,
    )
    try:
        try:
            runtime.process_store.get(process.process_id, session_id="other-session")
        except PermissionError:
            pass
        else:
            raise AssertionError("cross-session process access must be rejected")
    finally:
        process.terminate_tree()
