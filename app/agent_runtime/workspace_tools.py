from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolRegistry, ToolResult


_MAX_WRITE_CHARS = 1_000_000
_MAX_READ_FILE_BYTES = 1_000_000
_MAX_SEARCH_RESULTS = 200
_MAX_COMMAND_OUTPUT_CHARS = 20_000
_DEFAULT_COMMAND_TIMEOUT = 120
_MAX_COMMAND_TIMEOUT = 600
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")
_SKIP_DIR_NAMES = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules"}


def _safe_process_environment() -> dict[str, str]:
    output: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            continue
        output[name] = value
    return output


def _bounded_text(value: str, limit: int = _MAX_COMMAND_OUTPUT_CHARS) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _iter_workspace_files(root: Path):
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIR_NAMES]
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if not path.is_symlink() and path.is_file():
                yield path


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
        target.parent.mkdir(parents=True, exist_ok=True)
        context.raise_if_cancelled()
        target.write_text(text, encoding="utf-8")
        return ToolResult(
            ok=True,
            content=f"Wrote workspace file: {relative}",
            data={"path": relative, "chars": len(text)},
        )

    return AgentTool(
        name="write_workspace_text",
        description=(
            "Create or completely replace one UTF-8 text file inside the current workspace. "
            "Mutation is controlled by the current session permission mode."
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
            "Apply one precise text edit inside a workspace file by replacing an exact old_text block. "
            "The edit fails if the old block is absent or ambiguous. Mutation follows the current permission mode."
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


def workspace_command_tool() -> AgentTool:
    def run_command(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        raw_argv = arguments["argv"]
        if not isinstance(raw_argv, list) or not raw_argv:
            raise ValueError("argv must be a non-empty array")
        argv = [str(value) for value in raw_argv]
        if len(argv) > 256:
            raise ValueError("argv contains too many entries")
        if any(not value for value in argv):
            raise ValueError("argv entries must not be empty")
        if any(len(value) > 16_000 for value in argv):
            raise ValueError("an argv entry is too long")

        relative_cwd = str(arguments.get("cwd") or ".").strip() or "."
        cwd = context.resolve_workspace_path(relative_cwd)
        if not cwd.is_dir():
            raise ValueError("command cwd must be a workspace directory")

        timeout_seconds = int(arguments.get("timeout_seconds") or _DEFAULT_COMMAND_TIMEOUT)
        if not 1 <= timeout_seconds <= _MAX_COMMAND_TIMEOUT:
            raise ValueError(
                f"timeout_seconds must be within 1..{_MAX_COMMAND_TIMEOUT}"
            )
        stdin_text = str(arguments.get("stdin") or "")
        if len(stdin_text) > 256_000:
            raise ValueError("stdin exceeds 256,000 characters")

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                input=stdin_text if stdin_text else None,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                env=_safe_process_environment(),
                check=False,
            )
        except FileNotFoundError as exc:
            return ToolResult(ok=False, content=f"Executable not found: {argv[0]}", data={"error": str(exc)})
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_truncated = _bounded_text(str(exc.stdout or ""))
            stderr, stderr_truncated = _bounded_text(str(exc.stderr or ""))
            return ToolResult(
                ok=False,
                content=f"Command timed out after {timeout_seconds} seconds.",
                data={
                    "argv": argv,
                    "cwd": relative_cwd,
                    "timeout_seconds": timeout_seconds,
                    "stdout": stdout,
                    "stderr": stderr,
                    "output_truncated": stdout_truncated or stderr_truncated,
                },
            )

        stdout, stdout_truncated = _bounded_text(completed.stdout)
        stderr, stderr_truncated = _bounded_text(completed.stderr)
        content_parts = [f"exit={completed.returncode}"]
        if stdout:
            content_parts.append(f"stdout:\n{stdout}")
        if stderr:
            content_parts.append(f"stderr:\n{stderr}")
        return ToolResult(
            ok=completed.returncode == 0,
            content="\n".join(content_parts),
            data={
                "argv": argv,
                "cwd": relative_cwd,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": stdout_truncated or stderr_truncated,
            },
        )

    return AgentTool(
        name="run_workspace_command",
        description=(
            "Run one executable directly (no shell expansion) with argv inside a workspace directory. "
            "Use it for tests, linters, git, compilers, or project commands. API keys/tokens/password-like "
            "environment variables are stripped. Process execution is sensitive and follows the current permission mode."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "stdin": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=run_command,
        effect=ToolEffect.SENSITIVE,
    )


def legacy_workspace_write_note_tool() -> AgentTool:
    """Compatibility alias kept for sessions created by Loom 0.1.0."""

    current = workspace_write_tool()

    def write_note(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return current.handler(context, arguments)

    return AgentTool(
        name="write_workspace_note",
        description=(
            "Compatibility alias for write_workspace_text. Write a UTF-8 file inside the workspace. "
            "Mutation follows the current session permission mode."
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
    registry.register(workspace_command_tool())
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
