from __future__ import annotations

import anyio

from mcp import Client

from app.chatgpt_mcp import build_mcp_server
from app.remote_control import RemoteControlClient


class FakeBackend:
    def __init__(self) -> None:
        self.started_threads = []
        self.started_turns = []
        self.threads = {}

    def runtime_status(self):
        return {"model": "fake", "activeThreadIds": []}

    def project_list(self):
        return {"projects": [{"id": "project-1", "name": "Loom"}]}

    def thread_list(self, *, limit=100):
        return {"threads": []}

    def thread_read(self, thread_id):
        return self.threads[thread_id]

    def thread_start(self, *, workspace=None, project_id="", permission_mode=None):
        self.started_threads.append((project_id, permission_mode))
        record = {
            "id": "thread-1",
            "permissionMode": permission_mode,
            "status": "idle",
            "currentTurnId": None,
        }
        self.threads["thread-1"] = {"thread": record, "pendingApproval": None}
        return {"thread": record}

    def turn_start(self, thread_id, text, attachments=()):
        self.started_turns.append((thread_id, text))
        return {"turn": {"id": "turn-1", "status": "starting"}}

    def turn_steer(self, thread_id, turn_id, text, *, client_input_id=""):
        return {"threadId": thread_id, "turnId": turn_id, "accepted": True}

    def turn_interrupt(self, thread_id, turn_id):
        return {"threadId": thread_id, "turnId": turn_id, "requested": True}

    def approval_respond(
        self,
        thread_id,
        *,
        turn_id,
        request_id,
        call_id,
        decision,
    ):
        return {"accepted": True}


def test_chatgpt_mcp_exposes_only_agent_control_surface():
    async def scenario() -> None:
        backend = FakeBackend()
        server = build_mcp_server(RemoteControlClient(backend))
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}

            assert set(tools) == {
                "loom_status",
                "loom_projects_list",
                "loom_threads_list",
                "loom_thread_read",
                "loom_task_start",
                "loom_task_steer",
                "loom_task_stop",
                "loom_approval_respond",
            }
            assert tools["loom_status"].annotations.read_only_hint is True
            assert tools["loom_task_start"].annotations.read_only_hint is False
            assert tools["loom_approval_respond"].meta["ui"]["visibility"] == ["app"]

            result = await client.call_tool(
                "loom_task_start",
                {
                    "prompt": "run tests",
                    "project_id": "project-1",
                    "idempotency_key": "mcp-start-1",
                },
            )
            assert result.is_error is False
            assert result.structured_content["ok"] is True
            assert backend.started_threads == [("project-1", "approval")]

    anyio.run(scenario)


def test_chatgpt_mcp_source_does_not_import_agent_runtime():
    from pathlib import Path
    import app.chatgpt_mcp.server as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from app.agent_runtime" not in source
    assert "import app.agent_runtime" not in source
