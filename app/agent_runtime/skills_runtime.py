from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .contracts import ToolEffect
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
            runtime_home = root.parent
            resolved_roots = (
                runtime_home / "skills",
                Path.home() / ".agents" / "skills",
            )
        else:
            resolved_roots = tuple(Path(item).expanduser() for item in skill_roots)
        self.skill_manager = SkillManager(user_roots=resolved_roots)

        for tool in (self._skill_search_tool(), self._skill_load_tool()):
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
        return ToolResult(
            ok=True,
            content=(
                f"Loaded skill {skill.name!r} from {skill.scope.value} scope. "
                "Treat the following as reusable workflow instructions; all tool calls still cross Loom permissions.\n\n"
                f"{body}"
            ),
            data={
                **self._skill_record(skill),
                "loaded": True,
                "discovery_error_count": len(snapshot.errors),
            },
        )

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
