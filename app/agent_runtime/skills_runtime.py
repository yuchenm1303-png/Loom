from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from app.ai import AIMessage, MessageRole
from .contracts import ToolEffect
from .memory_store import redact_secrets
from .skills import SkillDefinition, SkillManager
from .tool_search_runtime import ToolSearchRuntime
from .tools import AgentTool, ToolContext, ToolExposure, ToolResult


_MAX_SKILL_RESOURCE_BYTES = 256 * 1024
_MAX_SKILL_RESOURCE_ENTRIES = 100
_PRIVATE_BUNDLE_FILES = {".loom-skill.json"}


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

        for tool in (self._skill_search_tool(), self._skill_load_tool(), self._skill_resource_tool()):
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
            name="skill_resource",
            description=(
                "List or read supporting files from an already installed skill bundle. "
                "This is read-only, rejects path escapes and symlinks, bounds file size, and never executes bundle content."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact installed skill name.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["list", "read"],
                        "description": "List bundle files or read one UTF-8 text resource.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Bundle-relative path. Defaults to '.' for list.",
                    },
                },
                "required": ["name", "action"],
                "additionalProperties": False,
            },
            handler=self._skill_resource,
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
        bundle_root = skill.path.parent.resolve()
        active = context.services.get("active_skills")
        if active is not None:
            content = (
                f"Loaded skill {skill.name} ({skill.scope.value}).\n"
                f"Bundle root: {bundle_root}\n"
                "Supporting files are inert. Read them with skill_resource; any command execution still uses Loom's normal "
                "sensitive exec/process tools and permission checks.\n\n"
                f"{body}"
            )
            if sum(len(v) for k, v in active.items() if k != skill.name) + len(content) > 32_768:
                return ToolResult(False, "Active skill context budget exceeded; finish existing workflows before loading another skill.")
            active[skill.name] = content
        return ToolResult(
            ok=True,
            content=(
                f"Loaded skill {skill.name!r} from {skill.scope.value} scope. "
                "Treat the following as reusable workflow instructions; all tool calls still cross Loom permissions.\n"
                f"Bundle root: {bundle_root}\n"
                "Supporting files are inert on install and load. Use skill_resource to inspect them; running a bundled script "
                "must go through Loom's normal exec/process permission and sandbox boundary.\n\n"
                f"{body}"
            ),
            data={
                **self._skill_record(skill),
                "loaded": True,
                "bundle_root": str(bundle_root),
                "discovery_error_count": len(snapshot.errors),
            },
        )

    def _skill_resource(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        name = str(arguments.get("name") or "").strip()
        action = str(arguments.get("action") or "").strip().casefold()
        relative_path = str(arguments.get("path") or ".").strip() or "."
        if not name:
            raise ValueError("skill name must not be empty")
        if action not in {"list", "read"}:
            raise ValueError("skill resource action must be 'list' or 'read'")

        snapshot = self.skill_manager.discover(context.workspace)
        skill = snapshot.get(name)
        if skill is None:
            return ToolResult(False, f"Skill not found: {name}", data={"name": name, "available": False})
        bundle_root = skill.path.parent.resolve(strict=True)
        target = self._resolve_bundle_path(bundle_root, relative_path)

        if action == "list":
            if not target.is_dir():
                return ToolResult(False, f"Skill resource is not a directory: {relative_path}")
            entries: list[dict[str, object]] = []
            for child in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
                if child.name in _PRIVATE_BUNDLE_FILES or child.name.startswith(".git"):
                    continue
                if child.is_symlink():
                    entries.append({"name": child.name, "kind": "blocked-symlink"})
                    continue
                kind = "dir" if child.is_dir() else "file" if child.is_file() else "other"
                record: dict[str, object] = {"name": child.name, "kind": kind}
                if child.is_file():
                    try:
                        record["bytes"] = child.stat().st_size
                    except OSError:
                        pass
                entries.append(record)
                if len(entries) >= _MAX_SKILL_RESOURCE_ENTRIES:
                    break
            content = "\n".join(
                f"{entry['kind']:15} {entry['name']}"
                + (f" ({entry['bytes']} bytes)" if "bytes" in entry else "")
                for entry in entries
            ) or "(empty skill resource directory)"
            return ToolResult(
                True,
                content,
                data={"name": skill.name, "path": relative_path, "entries": entries},
            )

        if relative_path in {"", "."}:
            return ToolResult(False, "skill_resource read requires a file path")
        if not target.is_file() or target.is_symlink():
            return ToolResult(False, f"Skill resource is not a regular file: {relative_path}")
        size = target.stat().st_size
        if size > _MAX_SKILL_RESOURCE_BYTES:
            return ToolResult(False, f"Skill resource exceeds {_MAX_SKILL_RESOURCE_BYTES} bytes: {relative_path}")
        payload = target.read_bytes()
        if b"\x00" in payload:
            return ToolResult(False, f"Skill resource is binary and cannot be read as text: {relative_path}")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(False, f"Skill resource is not UTF-8 text: {relative_path}")
        text = redact_secrets(text)
        return ToolResult(
            True,
            text,
            data={"name": skill.name, "path": relative_path, "bytes": size, "redacted": True},
        )

    @staticmethod
    def _resolve_bundle_path(bundle_root: Path, relative_path: str) -> Path:
        normalized = PurePosixPath(str(relative_path).replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("skill resource path must stay inside the bundle")
        target = bundle_root.joinpath(*normalized.parts).resolve(strict=True)
        try:
            target.relative_to(bundle_root)
        except ValueError as exc:
            raise ValueError("skill resource path escapes the bundle") from exc
        return target

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
