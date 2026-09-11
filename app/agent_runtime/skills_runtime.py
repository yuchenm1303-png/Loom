from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from app.ai import AIMessage, MessageRole
from .contracts import ToolEffect
from .skill_bundle import list_skill_files, read_skill_resource, stage_skill_bundle
from .skills import SkillDefinition, SkillManager
from .tool_search_runtime import ToolSearchRuntime
from .tools import AgentTool, ToolContext, ToolExposure, ToolResult


class SkillRuntime(ToolSearchRuntime):
    """Top-level runtime layer for on-demand Codex-compatible skills."""

    def __init__(
        self,
        *args: Any,
        skill_roots: Sequence[str | Path] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if skill_roots is None:
            root = Path(getattr(self.store, "root", "")).expanduser().resolve()
            try:
                runtime_home = root.parents[1]
            except IndexError:
                runtime_home = root.parent
            resolved_roots = (
                runtime_home / "skills",
                Path.home() / ".agents" / "skills",
            )
        else:
            resolved_roots = tuple(Path(item).expanduser() for item in skill_roots)
        self.skill_manager = SkillManager(user_roots=resolved_roots)

        for tool in (
            self._skill_search_tool(),
            self._skill_load_tool(),
            self._skill_resource_tool(),
            self._skill_stage_tool(),
        ):
            if self.tools.get(tool.name) is not None:
                raise ValueError(f"skill runtime conflicts with existing tool: {tool.name}")
            self.tools.register(tool)

    def _skill_search_tool(self) -> AgentTool:
        return AgentTool(
            name="skill_search",
            description=(
                "Search reusable SKILL.md workflows available to this workspace. "
                "Search returns metadata only; call skill_load with an exact skill name before following its instructions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Task or workflow capability to find.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum matches, from 1 to 20. Defaults to 5.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=self._search_skills,
            effect=ToolEffect.READ_ONLY,
            exposure=ToolExposure.DIRECT,
        )

    def _skill_load_tool(self) -> AgentTool:
        return AgentTool(
            name="skill_load",
            description=(
                "Load the full instructions for one discovered SKILL.md by exact skill name. "
                "Skill instructions may guide tool use but never bypass Loom permission or approval checks."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact skill name returned by skill_search.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=self._load_skill,
            effect=ToolEffect.READ_ONLY,
            exposure=ToolExposure.DIRECT,
        )

    def _skill_resource_tool(self) -> AgentTool:
        return AgentTool(
            name="skill_read_resource",
            description=(
                "Read one UTF-8 text resource bundled with a discovered skill, such as a reference, template, "
                "configuration example, or script source. Binary assets should be staged instead."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact discovered skill name.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path relative to the skill directory.",
                    },
                },
                "required": ["name", "path"],
                "additionalProperties": False,
            },
            handler=self._read_skill_resource,
            effect=ToolEffect.READ_ONLY,
            exposure=ToolExposure.DIRECT,
        )

    def _skill_stage_tool(self) -> AgentTool:
        return AgentTool(
            name="skill_stage_bundle",
            description=(
                "Copy one discovered skill bundle into the current workspace under .loom/skill-runs/<name>. "
                "Use this before running bundled scripts or consuming binary assets. Staging does not execute code; "
                "subsequent process/tool calls remain subject to Loom permissions and sandboxing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact discovered skill name.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=self._stage_skill_bundle,
            effect=ToolEffect.MUTATING,
            exposure=ToolExposure.DIRECT,
        )

    def _search_skills(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit", 5))
        snapshot = self.skill_manager.discover(context.workspace)
        matches = snapshot.search(query, limit=limit)
        records = [self._skill_record(skill) for skill in matches]
        if records:
            content = "Matching skills: " + ", ".join(skill.name for skill in matches)
        else:
            content = f"No skills matched: {query}"
        return ToolResult(
            ok=True,
            content=content,
            data={
                "query": query,
                "count": len(records),
                "skills": records,
                "discovery_error_count": len(snapshot.errors),
            },
        )

    def _load_skill(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        name = str(arguments.get("name") or "").strip()
        if not name:
            raise ValueError("skill name must not be empty")
        snapshot = self.skill_manager.discover(context.workspace)
        skill = snapshot.get(name)
        if skill is None:
            return ToolResult(
                ok=False,
                content=f"Skill not found: {name}",
                data={"name": name, "available": False},
            )
        instructions = self.skill_manager.load(skill)
        body = instructions or "(This skill has no body instructions.)"
        files = list_skill_files(skill, limit=80)
        active = context.services.get("active_skills")
        if active is not None:
            content = f"Loaded skill {skill.name} ({skill.scope.value}):\n{body}"
            if sum(len(v) for k, v in active.items() if k != skill.name) + len(content) > 32_768:
                return ToolResult(False, "Active skill context budget exceeded; finish existing workflows before loading another skill.")
            active[skill.name] = content
        bundle_hint = ""
        if len(files) > 1 or (files and files[0] != "SKILL.md"):
            bundle_hint = (
                "\n\nThis skill has bundled files. Use skill_read_resource for UTF-8 text resources, "
                "or skill_stage_bundle before using bundled scripts/binary assets. Staging and execution "
                "remain inside Loom's normal permission and sandbox boundaries."
            )
        return ToolResult(
            ok=True,
            content=(
                f"Loaded skill {skill.name!r} from {skill.scope.value} scope. "
                "Treat the following as reusable workflow instructions; all tool calls still cross Loom permissions.\n\n"
                f"{body}{bundle_hint}"
            ),
            data={
                **self._skill_record(skill),
                "loaded": True,
                "bundle_files": list(files),
                "bundle_file_count_returned": len(files),
                "discovery_error_count": len(snapshot.errors),
            },
        )

    def _read_skill_resource(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        name = str(arguments.get("name") or "").strip()
        relative_path = str(arguments.get("path") or "").strip()
        snapshot = self.skill_manager.discover(context.workspace)
        skill = snapshot.get(name)
        if skill is None:
            return ToolResult(False, f"Skill not found: {name}", data={"name": name, "available": False})
        text = read_skill_resource(skill, relative_path)
        return ToolResult(
            True,
            text,
            data={
                **self._skill_record(skill),
                "path": relative_path,
            },
        )

    def _stage_skill_bundle(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        name = str(arguments.get("name") or "").strip()
        snapshot = self.skill_manager.discover(context.workspace)
        skill = snapshot.get(name)
        if skill is None:
            return ToolResult(False, f"Skill not found: {name}", data={"name": name, "available": False})
        staged = stage_skill_bundle(skill, context.workspace)
        try:
            display_path = staged.relative_to(context.workspace).as_posix()
        except ValueError:
            display_path = str(staged)
        return ToolResult(
            True,
            f"Staged skill {skill.name!r} at {display_path}. Bundled code has not been executed.",
            data={
                **self._skill_record(skill),
                "staged": True,
                "workspace_path": display_path,
            },
        )

    def _request_context_messages(self, session, step, envelope):
        messages = super()._request_context_messages(session, step, envelope)
        if not session.active_skills:
            return messages
        content = (
            "Previously loaded skill snapshots, retained across compaction and resume. "
            "They guide this workflow and do not override current user instructions or runtime permissions.\n\n"
            + "\n\n".join(session.active_skills.values())
        )
        return (*messages, AIMessage(role=MessageRole.SYSTEM, name="loom_active_skills", content=content))

    @staticmethod
    def _skill_record(skill: SkillDefinition) -> dict[str, str]:
        try:
            relative = skill.path.relative_to(skill.root)
        except ValueError:
            relative = Path(skill.path.name)
        return {
            "name": skill.name,
            "description": skill.description,
            "short_description": skill.short_description,
            "scope": skill.scope.value,
            "source": f"{skill.scope.value}:{relative.as_posix()}",
        }

    def skills_status(self, session_id: str) -> dict[str, object]:
        session = self.store.load(session_id)
        snapshot = self.skill_manager.discover(Path(session.workspace_dir))
        return {
            "count": len(snapshot.skills),
            "skills": [self._skill_record(skill) for skill in snapshot.skills],
            "errors": list(snapshot.errors),
        }


__all__ = ["SkillRuntime"]
