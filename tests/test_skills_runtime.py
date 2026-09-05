from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    SkillError,
    SkillManager,
    SkillRuntime,
    SkillScope,
    ToolRegistry,
    parse_skill_document,
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


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str,
    body: str,
    short_description: str = "",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    extra = f"short_description: {short_description}\n" if short_description else ""
    path = directory / "SKILL.md"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra}"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def _request_text(request) -> str:
    parts = []
    for message in request.messages:
        content = message.content
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.append(str(content))
    return "\n".join(parts)


def test_parse_skill_document_accepts_codex_frontmatter_and_redacts_body_secret():
    parsed = parse_skill_document(
        "---\n"
        "name: deploy-helper\n"
        "description: Deploy the current project safely\n"
        "short_description: Safe deploy workflow\n"
        "---\n"
        "Use token sk-abcdefghijklmnopqrstuvwxyz123456 and then deploy.\n",
        default_name="ignored",
    )

    assert parsed.name == "deploy-helper"
    assert parsed.description == "Deploy the current project safely"
    assert parsed.short_description == "Safe deploy workflow"
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in parsed.instructions


def test_parse_skill_document_rejects_missing_description():
    with pytest.raises(SkillError):
        parse_skill_document("---\nname: demo\n---\nDo work.\n", default_name="demo")


def test_repo_skill_precedes_parent_and_user_skill(tmp_path: Path):
    project = tmp_path / "project"
    nested = project / "packages" / "app"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()

    user_root = tmp_path / "user-skills"
    _write_skill(
        user_root,
        "review",
        description="User review workflow",
        body="USER BODY",
    )
    _write_skill(
        project / ".agents" / "skills",
        "review",
        description="Project review workflow",
        body="PROJECT BODY",
    )
    nested_skill = _write_skill(
        nested / ".agents" / "skills",
        "review",
        description="Nearest review workflow",
        body="NEAREST BODY",
    )

    manager = SkillManager(user_roots=(user_root,))
    snapshot = manager.discover(nested)
    skill = snapshot.get("review")

    assert skill is not None
    assert skill.scope is SkillScope.REPO
    assert skill.path == nested_skill.resolve()
    assert manager.load(skill) == "NEAREST BODY"


def test_hidden_skill_directories_are_skipped(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    root = project / ".agents" / "skills"
    _write_skill(root / ".hidden", "secret-workflow", description="Hidden workflow", body="HIDDEN")
    _write_skill(root, "visible-workflow", description="Visible workflow", body="VISIBLE")

    snapshot = SkillManager().discover(project)

    assert snapshot.get("visible-workflow") is not None
    assert snapshot.get("secret-workflow") is None


def test_symlink_escape_is_skipped_when_supported(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    root = project / ".agents" / "skills"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside_skill = _write_skill(outside, "escaped", description="Escaped workflow", body="ESCAPED")
    link_dir = root / "linked"
    try:
        link_dir.symlink_to(outside_skill.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")

    snapshot = SkillManager().discover(project)

    assert snapshot.get("escaped") is None
    assert any("outside root" in error for error in snapshot.errors)


def test_skill_search_is_metadata_only_and_skill_load_reveals_body(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_root = tmp_path / "skills"
    body_marker = "BODY_ONLY_AFTER_EXPLICIT_LOAD"
    _write_skill(
        user_root,
        "release-check",
        description="Check a release before publishing",
        short_description="Release verification",
        body=body_marker,
    )

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="skill-search-1",
                        name="skill_search",
                        arguments={"query": "release verification"},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="skill-load-1",
                        name="skill_load",
                        arguments={"name": "release-check"},
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
        skill_roots=(user_root,),
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
        result = runtime.start_turn(session.session_id, "Use the release skill.")

        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "done"
        assert len(platform.requests) == 3
        assert body_marker not in _request_text(platform.requests[0])
        assert body_marker not in _request_text(platform.requests[1])
        assert body_marker in _request_text(platform.requests[2])
        assert {tool.name for tool in platform.requests[0].tools} >= {"skill_search", "skill_load"}
    finally:
        runtime.close()


def test_skills_status_reports_scope_relative_source(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "lint-project",
        description="Lint the current project",
        body="Run the project lint command.",
    )
    runtime = SkillRuntime(
        platform=RecordingPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
        skill_roots=(),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=workspace,
            permission_mode=PermissionMode.WORKSPACE,
        )
        status = runtime.skills_status(session.session_id)

        assert status["count"] == 1
        record = status["skills"][0]
        assert record["name"] == "lint-project"
        assert record["scope"] == "repo"
        assert record["source"] == "repo:lint-project/SKILL.md"
    finally:
        runtime.close()
