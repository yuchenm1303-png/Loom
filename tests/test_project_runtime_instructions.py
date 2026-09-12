"""Project Instructions are part of project turns, not old thread history."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai import AIMessage, MessageRole
from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server_project_move import ProjectMovableLoomAppServerService


PROJECT_CONTEXT_NAME = "loom_registered_project_instructions"


class SilentPlatform:
    def execute_chat(self, _profile_id, _request):  # pragma: no cover - never reached
        raise AssertionError("these tests inspect prepared requests only")


@pytest.fixture()
def service(tmp_path):
    workspace = tmp_path / "default"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    runtime = DurableAgentRuntime(
        platform=SilentPlatform(),
        store=store,
        tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    built = ProjectMovableLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    yield built
    runtime.close()


def _folder(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prepared_messages(service: ProjectMovableLoomAppServerService, thread_id: str) -> list[AIMessage]:
    session = service.runtime.get_session(thread_id)
    step = service.runtime._build_step_context(session, next_model_step=True)
    messages, _request_options = service.runtime._prepare_model_request(session, step, None)
    return list(messages)


def _project_context_messages(messages: list[AIMessage]) -> list[AIMessage]:
    return [message for message in messages if message.name == PROJECT_CONTEXT_NAME]


def test_project_instructions_are_injected_after_workspace_instructions(service, tmp_path):
    workspace = _folder(tmp_path, "loom")
    (workspace / "AGENTS.md").write_text("Prefer small focused patches.", encoding="utf-8")
    project = service.project_create({"root": str(workspace)})["project"]
    service.project_set_instructions(
        {
            "projectId": project["id"],
            "instructions": "Always commit directly to main for this repository.",
        }
    )
    thread = service.thread_start({"projectId": project["id"]})["thread"]

    messages = _prepared_messages(service, thread["id"])
    context_messages = _project_context_messages(messages)

    assert len(context_messages) == 1
    assert "Always commit directly to main" in context_messages[0].content
    names = [message.name for message in messages]
    assert names.index("loom_project_instructions") < names.index(PROJECT_CONTEXT_NAME)


def test_unfiled_threads_do_not_receive_project_instructions(service, tmp_path):
    project_workspace = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(project_workspace)})["project"]
    service.project_set_instructions({"projectId": project["id"], "instructions": "Project-only rule."})
    loose = service.thread_start({"workspace": str(_folder(tmp_path, "loose"))})["thread"]

    messages = _prepared_messages(service, loose["id"])

    assert _project_context_messages(messages) == []
    assert all("Project-only rule" not in str(message.content) for message in messages)


def test_project_instruction_marker_is_replaced_not_duplicated(service, tmp_path):
    workspace = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(workspace)})["project"]
    service.project_set_instructions({"projectId": project["id"], "instructions": "Fresh project rule."})
    thread = service.thread_start({"projectId": project["id"]})["thread"]
    session = service.runtime.get_session(thread["id"])

    stale_messages = [
        AIMessage(role=MessageRole.SYSTEM, content="core"),
        AIMessage(role=MessageRole.SYSTEM, name=PROJECT_CONTEXT_NAME, content="stale project rule"),
        AIMessage(role=MessageRole.USER, content="hello"),
    ]

    injected = service._inject_project_instruction_message(session, stale_messages)
    context_messages = _project_context_messages(injected)

    assert len(context_messages) == 1
    assert "Fresh project rule." in context_messages[0].content
    assert "stale project rule" not in context_messages[0].content
    assert injected[1].name == PROJECT_CONTEXT_NAME
    assert injected[2].role is MessageRole.USER


def test_active_turn_uses_the_starting_instruction_snapshot(service, tmp_path):
    workspace = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(workspace)})["project"]
    service.project_set_instructions({"projectId": project["id"], "instructions": "Starting rule."})
    thread = service.thread_start({"projectId": project["id"]})["thread"]
    session = service.runtime.get_session(thread["id"])

    service._snapshot_project_instruction_context(session)
    service.project_set_instructions({"projectId": project["id"], "instructions": "Next-turn rule."})

    messages = _prepared_messages(service, thread["id"])
    content = _project_context_messages(messages)[0].content
    assert "Starting rule." in content
    assert "Next-turn rule." not in content

    with service._guard:
        service._project_instruction_context_snapshots.pop(thread["id"], None)

    messages = _prepared_messages(service, thread["id"])
    content = _project_context_messages(messages)[0].content
    assert "Next-turn rule." in content
