from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from app.ai import AIMessage, MessageRole, ModelUsage
from app.agent_runtime import (
    AgentSession,
    AgentStatus,
    FileAgentSessionStore,
    PendingToolApproval,
    PermissionMode,
    ToolEffect,
)
from app.agent_runtime.storage import utc_now
from app.web_ui import LoomWebService, create_web_server


class FakeRuntime:
    def __init__(self, store: FileAgentSessionStore) -> None:
        self.store = store
        self.closed = False

    def create_session(self, profile_id: str, *, workspace_dir: str | Path, permission_mode: PermissionMode):
        now = utc_now()
        session = AgentSession(
            session_id=str(uuid.uuid4()),
            profile_id=profile_id,
            system_prompt="You are Loom in a UI test.",
            workspace_dir=str(Path(workspace_dir).resolve()),
            created_at=now,
            updated_at=now,
            permission_mode=permission_mode,
        )
        self.store.create(session)
        return session

    def get_session(self, session_id: str):
        return self.store.load(session_id)

    def recover_interrupted(self, session_id: str):
        session = self.store.load(session_id)
        session.status = AgentStatus.INTERRUPTED
        self.store.save(session)
        return session

    def start_turn(self, session_id: str, text: str):
        session = self.store.load(session_id)
        session.status = AgentStatus.RUNNING
        session.messages.append(AIMessage(role=MessageRole.USER, content=text))
        self.store.save(session)
        session.messages.append(AIMessage(role=MessageRole.ASSISTANT, content=f"Echo: {text}"))
        session.status = AgentStatus.COMPLETED
        session.final_text = f"Echo: {text}"
        session.usage = ModelUsage(input_tokens=4, output_tokens=3, total_tokens=7)
        self.store.save(session)
        return session

    def resume_approval(self, session_id: str, call_id: str, *, approved: bool):
        session = self.store.load(session_id)
        assert session.pending_approval is not None
        assert session.pending_approval.call_id == call_id
        session.pending_approval = None
        session.status = AgentStatus.COMPLETED
        session.messages.append(
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="Approved" if approved else "Denied",
            )
        )
        self.store.save(session)
        return session

    def cancel(self, session_id: str):
        session = self.store.load(session_id)
        session.status = AgentStatus.CANCELLED
        self.store.save(session)
        return session

    def set_permission_mode(self, session_id: str, mode: PermissionMode):
        session = self.store.load(session_id)
        session.permission_mode = mode
        self.store.save(session)
        return session

    def close(self) -> None:
        self.closed = True


def build_service(tmp_path: Path) -> tuple[LoomWebService, FakeRuntime, FileAgentSessionStore, Path]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = FakeRuntime(store)
    service = LoomWebService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.APPROVAL,
    )
    return service, runtime, store, workspace


def wait_idle(service: LoomWebService, session_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.snapshot(session_id)
        if not snapshot["active"]:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("UI background task did not become idle")


def test_service_creates_session_and_runs_real_runtime_adapter(tmp_path: Path) -> None:
    service, _runtime, _store, workspace = build_service(tmp_path)
    created = service.create_session(
        workspace=str(workspace),
        permission_mode=PermissionMode.WORKSPACE.value,
    )
    session_id = created["session"]["session_id"]
    assert created["session"]["workspace_dir"] == str(workspace.resolve())
    assert created["session"]["permission_mode"] == "workspace"

    accepted = service.start_turn(session_id, "hello from UI")
    assert accepted["accepted"] is True
    snapshot = wait_idle(service, session_id)
    assert [item["role"] for item in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][-1]["content"] == "Echo: hello from UI"
    assert snapshot["usage"]["total_tokens"] == 7


def test_service_exposes_and_resumes_pending_approval(tmp_path: Path) -> None:
    service, _runtime, store, workspace = build_service(tmp_path)
    created = service.create_session(workspace=str(workspace), permission_mode="approval")
    session_id = created["session"]["session_id"]
    session = store.load(session_id)
    session.status = AgentStatus.WAITING_APPROVAL
    session.pending_approval = PendingToolApproval(
        call_id="call-1",
        tool_name="run_workspace_command",
        arguments={"argv": ["python", "-V"]},
        effect=ToolEffect.SENSITIVE,
        reason="process execution requires approval",
    )
    store.save(session)

    snapshot = service.snapshot(session_id)
    assert snapshot["pending_approval"]["tool_name"] == "run_workspace_command"
    service.resume_approval(session_id, call_id="call-1", approved=False)
    snapshot = wait_idle(service, session_id)
    assert snapshot["pending_approval"] is None
    assert snapshot["messages"][-1]["content"] == "Denied"


def test_web_server_is_local_ui_and_rejects_cross_origin_post(tmp_path: Path) -> None:
    service, _runtime, _store, workspace = build_service(tmp_path)
    server = create_web_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(f"{base}/", timeout=2) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert "Build with Loom" in html
            assert response.headers["X-Frame-Options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

        with urllib.request.urlopen(f"{base}/api/bootstrap", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["model"] == "test-model"
            assert payload["default_workspace"] == str(workspace.resolve())

        body = json.dumps(
            {"workspace": str(workspace), "permission_mode": "approval"}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/sessions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            created = json.loads(response.read().decode("utf-8"))
            assert response.status == 201
            assert created["session"]["workspace_dir"] == str(workspace.resolve())

        evil_request = urllib.request.Request(
            f"{base}/api/sessions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": "http://evil.example",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(evil_request, timeout=2)
        assert exc_info.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_service_rejects_missing_workspace(tmp_path: Path) -> None:
    service, _runtime, _store, _workspace = build_service(tmp_path)
    with pytest.raises(ValueError, match="Workspace does not exist"):
        service.create_session(
            workspace=str(tmp_path / "missing"),
            permission_mode="approval",
        )
