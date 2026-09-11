from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    SkillRuntime,
    ToolRegistry,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class RecordingPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _request_text(request) -> str:
    parts = []
    for message in request.messages:
        content = message.content
        parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


def _write_skill(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: bundle-demo\n"
        "description: Demonstrate bundle resources\n"
        "---\n"
        "Read references/guide.md before working.\n",
        encoding="utf-8",
    )
    references = root / "references"
    references.mkdir()
    references.joinpath("guide.md").write_text(
        "Use token sk-abcdefghijklmnopqrstuvwxyz123456 only as a redaction test.\n",
        encoding="utf-8",
    )


def test_skill_resource_is_read_only_and_redacts_text(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_root = tmp_path / "skills" / "bundle-demo"
    _write_skill(skill_root)
    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="load", name="skill_load", arguments={"name": "bundle-demo"}),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="resource",
                        name="skill_resource",
                        arguments={"name": "bundle-demo", "action": "read", "path": "references/guide.md"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime = SkillRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
        skill_roots=(tmp_path / "skills",),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=workspace,
            permission_mode=PermissionMode.APPROVAL,
        )
        result = runtime.start_turn(session.session_id, "Use the bundle demo skill.")

        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "done"
        assert len(platform.requests) == 3
        assert "skill_resource" in {tool.name for tool in platform.requests[0].tools}
        final_context = _request_text(platform.requests[2])
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in final_context
        assert "redaction test" in final_context
    finally:
        runtime.close()


def test_skill_resource_rejects_parent_traversal(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(ValueError, match="inside the bundle"):
        SkillRuntime._resolve_bundle_path(bundle, "../outside.txt")


def test_skill_resource_rejects_symlink_escape_when_supported(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = bundle / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not supported")
    with pytest.raises(ValueError, match="escapes the bundle"):
        SkillRuntime._resolve_bundle_path(bundle, "link.txt")
