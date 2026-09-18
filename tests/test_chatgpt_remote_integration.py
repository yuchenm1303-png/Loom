from __future__ import annotations

import time
from pathlib import Path

from app.ai import ModelResponse, ToolCall
from app.agent_runtime import (
    AgentTool,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.app_server import LoomAppServerService
from app.remote_control import RemoteControlClient, approval_fingerprint


class ScriptedPlatform:
    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class ServiceBackend:
    """Translate the channel-neutral client protocol into App Server service calls."""

    def __init__(self, service: LoomAppServerService) -> None:
        self.service = service

    def runtime_status(self):
        return self.service.runtime_status()

    def project_list(self):
        return self.service.project_list({})

    def thread_list(self, *, limit=100):
        return self.service.thread_list({"limit": limit})

    def thread_read(self, thread_id):
        return self.service.thread_read({"threadId": thread_id})

    def thread_start(
        self,
        *,
        workspace=None,
        project_id="",
        permission_mode=None,
        client_input_id="",
    ):
        params = {}
        if project_id:
            params["projectId"] = project_id
        elif workspace is not None:
            params["workspace"] = str(workspace)
        if permission_mode:
            params["permissionMode"] = permission_mode
        if client_input_id:
            params["clientInputId"] = client_input_id
        return self.service.thread_start(params)

    def turn_start(
        self,
        thread_id,
        text,
        attachments=(),
        *,
        client_input_id="",
    ):
        params = {"threadId": thread_id, "input": text}
        if attachments:
            params["attachments"] = list(attachments)
        if client_input_id:
            params["clientInputId"] = client_input_id
        return self.service.turn_start(params)

    def turn_steer(self, thread_id, turn_id, text, *, client_input_id=""):
        params = {"threadId": thread_id, "turnId": turn_id, "input": text}
        if client_input_id:
            params["clientInputId"] = client_input_id
        return self.service.turn_steer(params)

    def turn_interrupt(self, thread_id, turn_id):
        return self.service.turn_interrupt({"threadId": thread_id, "turnId": turn_id})

    def approval_respond(
        self,
        thread_id,
        *,
        turn_id,
        request_id,
        call_id,
        decision,
    ):
        return self.service.approval_respond(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "requestId": request_id,
                "callId": call_id,
                "decision": decision,
            }
        )


def wait_until(predicate, *, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for remote-control integration state")


def test_chatgpt_remote_cannot_bypass_real_loom_approval_boundary(tmp_path: Path):
    calls = []

    def sensitive_handler(_context: ToolContext, arguments):
        calls.append(str(arguments["value"]))
        return ToolResult(ok=True, content="sensitive action ran")

    sensitive = AgentTool(
        name="sensitive_remote_action",
        description="Sensitive action used to prove the approval boundary.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=sensitive_handler,
        effect=ToolEffect.SENSITIVE,
    )

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=ScriptedPlatform(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="remote-sensitive-call",
                            name="sensitive_remote_action",
                            arguments={"value": "approved-only"},
                        ),
                    )
                ),
                ModelResponse(text="remote task completed"),
            ]
        ),
        store=store,
        tools=ToolRegistry((sensitive,)),
        default_permission_mode=PermissionMode.APPROVAL,
        auto_drain_queue=False,
    )
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.APPROVAL,
    )
    remote = RemoteControlClient(ServiceBackend(service))
    try:
        project = service.project_create({"root": str(workspace)})["project"]
        started = remote.task_start(
            prompt="perform the sensitive action",
            project_id=project["id"],
            idempotency_key="remote-real-start",
        )
        thread_id = started["threadId"]

        pending_snapshot = wait_until(
            lambda: (
                snapshot
                if (snapshot := remote.thread_read(thread_id)).get("pendingApproval")
                else None
            )
        )
        pending = pending_snapshot["pendingApproval"]

        assert pending_snapshot["thread"]["permissionMode"] == "approval"
        assert "fingerprint" not in pending
        assert pending["callId"] == "remote-sensitive-call"
        assert calls == []

        fingerprint = approval_fingerprint(thread_id, pending)
        response = remote.approval_respond(
            thread_id=thread_id,
            fingerprint=fingerprint,
            decision="accept",
        )
        assert response["ok"] is True

        completed = wait_until(
            lambda: (
                snapshot
                if (snapshot := remote.thread_read(thread_id)).get("finalText")
                == "remote task completed"
                else None
            )
        )
        assert calls == ["approved-only"]
        assert completed["thread"]["status"] == "completed"
        assert completed["pendingApproval"] is None
    finally:
        runtime.close()
