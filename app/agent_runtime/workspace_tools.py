from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contracts import ToolEffect
from .diff_tracker import TurnDiffTracker
from .patch_tools import apply_patch_tool, get_turn_diff_tool
from .process_tools import managed_process_tools, workspace_command_tool
from .tools import AgentTool, ToolContext, ToolRegistry, ToolResult


_MAX_WRITE_CHARS = 1_000_000
_MAX_READ_FILE_BYTES = 1_000_000
_MAX_SEARCH_RESULTS = 200
_SKIP_DIR_NAMES = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules"}


def _iter_workspace_files(root: Path):
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIR_NAMES]
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if not path.is_symlink() and path.is_file():
                yield path


def _record_change(
    context: ToolContext,
    relative: str,
    *,
    before: str | None,
    after: str | None,
) -> None:
    tracker = context.service("diff_tracker", required=False)
    if isinstance(tracker, TurnDiffTracker):
        tracker.record_text_change(relative, before=before, after=after)


def workspace_search_tool() -> AgentTool:
    def search(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        query = str(arguments["query"] or "")
        relative = str(arguments.get("path") or ".").strip() or "."
        case_sensitive = bool(arguments.get("case_sensitive", False))
        if not query:
            raise ValueError("query must not be empty")
        root = context.resolve_workspace_path(relative)
        if not root.is_dir():
            raise ValueError("search path must be a directory")

        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        skipped_large = 0
        for path in _iter_workspace_files(root):
            context.raise_if_cancelled()
            try:
                if path.stat().st_size > _MAX_READ_FILE_BYTES:
                    skipped_large += 1
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                workspace_relative = path.relative_to(context.workspace.resolve()).as_posix()
                matches.append(
                    {
                        "path": workspace_relative,
                        "line": line_number,
                        "text": line[:500],
                    }
                )
                if len(matches) >= _MAX_SEARCH_RESULTS:
                    break
            if len(matches) >= _MAX_SEARCH_RESULTS:
                break

        return ToolResult(
            ok=True,
            content=f"Found {len(matches)} matching lines for {query!r}.",
            data={
                "matches": matches,
                "truncated": len(matches) >= _MAX_SEARCH_RESULTS,
                "skipped_large_files": skipped_large,
            },
        )

    return AgentTool(
        name="search_workspace_text",
        description=(
            "Search UTF-8 text files recursively inside the current workspace. "
            "Use this before editing when you need to find symbols, errors, or references."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "case_sensitive": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search,
        effect=ToolEffect.READ_ONLY,
    )


def workspace_write_tool() -> AgentTool:
    def write_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        relative = str(arguments["path"] or "").strip()
        text = str(arguments["text"] or "")
        if not relative:
            raise ValueError("path must not be empty")
        if len(text) > _MAX_WRITE_CHARS:
            raise ValueError(f"workspace text exceeds {_MAX_WRITE_CHARS:,} characters")
        target = context.resolve_workspace_path(relative)
        before = None
        if target.exists():
            if not target.is_file():
                raise ValueError("workspace write target must be a regular file")
            if target.stat().st_size > _MAX_READ_FILE_BYTES:
                raise ValueError("existing workspace text file exceeds editable size limit")
            before = target.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        context.raise_if_cancelled()
        target.write_text(text, encoding="utf-8")
        _record_change(context, relative, before=before, after=text)
        return ToolResult(
            ok=True,
            content=f"Wrote workspace file: {relative}",
            data={"path": relative, "chars": len(text)},
        )

    return AgentTool(
        name="write_workspace_text",
        description=(
            "Create or completely replace one UTF-8 text file inside the current workspace. "
            "The mutation is tracked in the current turn diff."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["path", "text"],
            "additionalProperties": False,
        },
        handler=write_text,
        effect=ToolEffect.MUTATING,
    )


def workspace_replace_tool() -> AgentTool:
    def replace_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        relative = str(arguments["path"] or "").strip()
        old_text = str(arguments["old_text"] or "")
        new_text = str(arguments["new_text"] or "")
        if not relative:
            raise ValueError("path must not be empty")
        if not old_text:
            raise ValueError("old_text must not be empty")
        target = context.resolve_workspace_path(relative)
        if not target.is_file():
            return ToolResult(ok=False, content=f"Workspace file does not exist: {relative}")
        if target.stat().st_size > _MAX_READ_FILE_BYTES:
            return ToolResult(ok=False, content="Workspace text file exceeds the editable size limit")
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            return ToolResult(ok=False, content="old_text was not found; no file was changed")
        if count > 1:
            return ToolResult(
                ok=False,
                content=f"old_text matched {count} locations; provide a more specific exact block",
            )
        updated = text.replace(old_text, new_text, 1)
        if len(updated) > _MAX_WRITE_CHARS:
            raise ValueError(f"updated workspace text exceeds {_MAX_WRITE_CHARS:,} characters")
        context.raise_if_cancelled()
        target.write_text(updated, encoding="utf-8")
        _record_change(context, relative, before=text, after=updated)
        return ToolResult(
            ok=True,
            content=f"Replaced one exact block in: {relative}",
            data={
                "path": relative,
                "old_chars": len(old_text),
                "new_chars": len(new_text),
            },
        )

    return AgentTool(
        name="replace_workspace_text",
        description=(
            "Compatibility precision edit: replace one exact text block inside a workspace file. "
            "It fails closed if the old block is absent or ambiguous and is tracked in the turn diff. "
            "Prefer apply_patch for multi-file edits."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
        handler=replace_text,
        effect=ToolEffect.MUTATING,
    )


def legacy_workspace_write_note_tool() -> AgentTool:
    """Compatibility alias kept for sessions created by Loom 0.1.0."""

    current = workspace_write_tool()

    def write_note(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return current.handler(context, arguments)

    return AgentTool(
        name="write_workspace_note",
        description=(
            "Compatibility alias for write_workspace_text. Write a UTF-8 file inside the workspace."
        ),
        input_schema=current.input_schema,
        handler=write_note,
        effect=ToolEffect.MUTATING,
    )


def loom_default_tools() -> ToolRegistry:
    from .builtin_tools import builtin_read_only_tools

    registry = builtin_read_only_tools()
    registry.register(workspace_search_tool())
    registry.register(workspace_write_tool())
    registry.register(workspace_replace_tool())
    registry.register(apply_patch_tool())
    registry.register(get_turn_diff_tool())
    for tool in managed_process_tools():
        registry.register(tool)
    registry.register(legacy_workspace_write_note_tool())
    return registry


__all__ = [
    "legacy_workspace_write_note_tool",
    "loom_default_tools",
    "workspace_command_tool",
    "workspace_replace_tool",
    "workspace_search_tool",
    "workspace_write_tool",
]
