from __future__ import annotations

import json
import sys

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
)
from app.agent_runtime.storage import session_from_dict, session_to_dict
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    ModelBinding,
    ModelResponse,
    ModelUsage,
    ProviderAdapter,
    ProviderConnection,
    ToolCall,
)


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _bind_workspace(store, session, workspace):
    session.workspace_dir = str(workspace.resolve())
    store.save(session)
    return session


def test_generic_ai_configuration_has_no_commerce_role():
    connection = ProviderConnection(
        provider_id="test",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.invalid/v1",
    )
    configuration = AIConfiguration.build(
        roles=(AGENT_FAST_ROLE,),
        providers=(connection,),
        bindings=(
            ModelBinding(
                role_id=AGENT_FAST_ROLE.role_id,
                provider_id=connection.provider_id,
                model="test-model",
                capabilities=AGENT_FAST_ROLE.required_capabilities,
            ),
        ),
    )
    assert [profile.profile_id for profile in configuration.profiles.all()] == ["agent.fast"]


def test_agent_tool_loop_completes(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="calc-1", name="calculator", arguments={"expression": "123 * 456"}),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=4, total_tokens=14),
            ),
            ModelResponse(
                text="56088",
                usage=ModelUsage(input_tokens=20, output_tokens=2, total_tokens=22),
            ),
        ]
    )
    store = FileAgentSessionStore(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id)

    result = runtime.start_turn(session.session_id, "Use the calculator for 123 * 456.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "56088"
    assert result.usage.total_tokens == 36
    kinds = [event.kind for event in store.events(session.session_id)]
    assert AgentEventKind.TOOL_REQUESTED in kinds
    assert AgentEventKind.TOOL_COMPLETED in kinds
    assert AgentEventKind.TURN_COMPLETED in kinds
    requested = [event for event in store.events(session.session_id) if event.kind is AgentEventKind.MODEL_REQUESTED]
    assert requested[-1].data["step_id"]


def test_native_workspace_binding_is_persisted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform([])
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())

    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )
    loaded = store.load(session.session_id)

    assert loaded.workspace_dir == str(project.resolve())
    assert loaded.permission_mode is PermissionMode.WORKSPACE
    assert not (store.session_dir(session.session_id) / "workspace").exists()


def test_workspace_search_runs_without_approval(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "alpha.py").write_text("def target_symbol():\n    return 42\n", encoding="utf-8")
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-1",
                        name="search_workspace_text",
                        arguments={"query": "target_symbol"},
                    ),
                )
            ),
            ModelResponse(text="Found it."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = _bind_workspace(
        store,
        runtime.create_session(AGENT_FAST_ROLE.role_id),
        project,
    )

    result = runtime.start_turn(session.session_id, "Find target_symbol.")

    assert result.status is AgentStatus.COMPLETED
    events = store.events(session.session_id)
    completed = [event for event in events if event.kind is AgentEventKind.TOOL_COMPLETED]
    assert completed
    assert completed[-1].data["data"]["matches"][0]["path"] == "alpha.py"


def test_mutating_workspace_write_requires_approval_by_default(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-1",
                        name="write_workspace_text",
                        arguments={"path": "notes/hello.txt", "text": "hello Loom"},
                    ),
                )
            ),
            ModelResponse(text="Done."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=project)

    waiting = runtime.start_turn(session.session_id, "Write a workspace file.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert waiting.pending_approval is not None
    assert not (project / "notes" / "hello.txt").exists()

    result = runtime.resume_approval(
        session.session_id,
        waiting.pending_approval.call_id,
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    assert (project / "notes" / "hello.txt").read_text(encoding="utf-8") == "hello Loom"


def test_workspace_mode_auto_allows_file_write(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-workspace",
                        name="write_workspace_text",
                        arguments={"path": "allowed.txt", "text": "workspace mode"},
                    ),
                )
            ),
            ModelResponse(text="Done."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "Write the file.")

    assert result.status is AgentStatus.COMPLETED
    assert (project / "allowed.txt").read_text(encoding="utf-8") == "workspace mode"
    assert AgentEventKind.TOOL_APPROVAL_REQUIRED not in [
        event.kind for event in store.events(session.session_id)
    ]


def test_workspace_mode_still_requires_approval_for_process_execution(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="cmd-workspace",
                        name="run_workspace_command",
                        arguments={"argv": [sys.executable, "-c", "print('ok')"]},
                    ),
                )
            ),
            ModelResponse(text="Done."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )

    waiting = runtime.start_turn(session.session_id, "Run command.")

    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert waiting.pending_approval.tool_name == "run_workspace_command"


def test_full_access_runs_sensitive_command_without_approval(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="cmd-full",
                        name="run_workspace_command",
                        arguments={"argv": [sys.executable, "-c", "print('full-access-ok')"]},
                    ),
                )
            ),
            ModelResponse(text="Command passed."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.FULL_ACCESS,
    )

    result = runtime.start_turn(session.session_id, "Run command.")

    assert result.status is AgentStatus.COMPLETED
    completed = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TOOL_COMPLETED
        and event.data.get("tool") == "run_workspace_command"
    ]
    assert completed[-1].data["data"]["returncode"] == 0
    assert "full-access-ok" in completed[-1].data["data"]["stdout"]
    assert AgentEventKind.TOOL_APPROVAL_REQUIRED not in [
        event.kind for event in store.events(session.session_id)
    ]


def test_read_only_denies_mutation_without_prompt_or_file_change(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-denied",
                        name="write_workspace_text",
                        arguments={"path": "blocked.txt", "text": "nope"},
                    ),
                )
            ),
            ModelResponse(text="The write was blocked."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.READ_ONLY,
    )

    result = runtime.start_turn(session.session_id, "Write a file.")

    assert result.status is AgentStatus.COMPLETED
    assert not (project / "blocked.txt").exists()
    events = store.events(session.session_id)
    denied = [event for event in events if event.kind is AgentEventKind.TOOL_DENIED]
    assert denied[-1].data["source"] == "permission"
    assert AgentEventKind.TOOL_APPROVAL_REQUIRED not in [event.kind for event in events]


def test_permission_mode_change_is_persisted(tmp_path):
    platform = ScriptedPlatform([])
    store = FileAgentSessionStore(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id)

    updated = runtime.set_permission_mode(session.session_id, PermissionMode.FULL_ACCESS)

    assert updated.permission_mode is PermissionMode.FULL_ACCESS
    assert store.load(session.session_id).permission_mode is PermissionMode.FULL_ACCESS
    changes = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.PERMISSION_CHANGED
    ]
    assert changes[-1].data == {"previous": "approval", "current": "full-access"}


def test_v1_session_snapshot_defaults_to_approval_mode(tmp_path):
    platform = ScriptedPlatform([])
    store = FileAgentSessionStore(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id)
    payload = session_to_dict(session)
    payload["version"] = 1
    payload.pop("permission_mode")

    restored = session_from_dict(json.loads(json.dumps(payload)))

    assert restored.permission_mode is PermissionMode.APPROVAL


def test_precise_replace_fails_closed_on_ambiguous_match(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "sample.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="replace-1",
                        name="replace_workspace_text",
                        arguments={
                            "path": "sample.txt",
                            "old_text": "same",
                            "new_text": "changed",
                        },
                    ),
                )
            ),
            ModelResponse(text="No change made."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = _bind_workspace(
        store,
        runtime.create_session(AGENT_FAST_ROLE.role_id),
        project,
    )

    waiting = runtime.start_turn(session.session_id, "Replace the text.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    result = runtime.resume_approval(
        session.session_id,
        waiting.pending_approval.call_id,
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    assert target.read_text(encoding="utf-8") == "same\nsame\n"
    failed = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TOOL_FAILED
    ]
    assert failed
    assert "matched 2 locations" in failed[-1].data["content"]


def test_command_execution_requires_approval_and_captures_output(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="cmd-1",
                        name="run_workspace_command",
                        arguments={
                            "argv": [sys.executable, "-c", "print('command-ok')"],
                            "cwd": ".",
                            "timeout_seconds": 30,
                        },
                    ),
                )
            ),
            ModelResponse(text="Command passed."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = _bind_workspace(
        store,
        runtime.create_session(AGENT_FAST_ROLE.role_id),
        project,
    )

    waiting = runtime.start_turn(session.session_id, "Run the validation command.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert waiting.pending_approval is not None
    assert waiting.pending_approval.tool_name == "run_workspace_command"

    result = runtime.resume_approval(
        session.session_id,
        waiting.pending_approval.call_id,
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    completed = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TOOL_COMPLETED
        and event.data.get("tool") == "run_workspace_command"
    ]
    assert completed
    assert completed[-1].data["data"]["returncode"] == 0
    assert "command-ok" in completed[-1].data["data"]["stdout"]


def test_command_tool_strips_secret_environment(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("LOOM_API_KEY", "must-not-leak")
    script = "import os; print(os.environ.get('LOOM_API_KEY', 'missing'))"
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="cmd-secret",
                        name="run_workspace_command",
                        arguments={"argv": [sys.executable, "-c", script]},
                    ),
                )
            ),
            ModelResponse(text="Secret was not exposed."),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = _bind_workspace(
        store,
        runtime.create_session(AGENT_FAST_ROLE.role_id),
        project,
    )

    waiting = runtime.start_turn(session.session_id, "Check the environment.")
    result = runtime.resume_approval(
        session.session_id,
        waiting.pending_approval.call_id,
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    completed = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TOOL_COMPLETED
        and event.data.get("tool") == "run_workspace_command"
    ]
    assert completed[-1].data["data"]["stdout"].strip() == "missing"
