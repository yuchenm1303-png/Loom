"""Codex-parity project instruction discovery and model-visible rendering.

Discovery walks from the project root to the effective cwd, choosing at most one
instruction file per directory. Deeper files therefore appear later and can
refine shallower rules. Rendering mirrors Codex's contextual-user fragment
markers so project documentation stays recognisable after history projection.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT_MARKERS = (".git",)
DEFAULT_INSTRUCTION_NAMES = ("AGENTS.override.md", "AGENTS.md")
PROJECT_DOC_SEPARATOR = "--- project-doc ---"
AGENTS_FRAGMENT_START = "# AGENTS.md instructions"
AGENTS_FRAGMENT_END = "</INSTRUCTIONS>"


@dataclass(frozen=True, slots=True)
class ProjectInstruction:
    path: Path
    text: str
    truncated: bool = False


class InstructionLoader:
    def __init__(
        self,
        max_bytes: int = 32_768,
        *,
        root_markers: Iterable[str] = DEFAULT_ROOT_MARKERS,
        fallback_names: Iterable[str] = (),
    ) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self.root_markers = tuple(
            marker for marker in (str(item).strip() for item in root_markers) if marker
        )
        self.fallback_names = tuple(
            name for name in (str(item).strip() for item in fallback_names) if name
        )

    def _project_root(self, cwd: Path) -> Path:
        # Codex treats an empty marker list as "do not walk upward".
        if not self.root_markers:
            return cwd
        for directory in (cwd, *cwd.parents):
            if any((directory / marker).exists() for marker in self.root_markers):
                return directory
        return cwd

    @staticmethod
    def _directories(root: Path, cwd: Path) -> tuple[Path, ...]:
        directories = [cwd]
        while directories[-1] != root:
            parent = directories[-1].parent
            if parent == directories[-1]:
                break
            directories.append(parent)
        return tuple(reversed(directories))

    def _candidate_names(self) -> tuple[str, ...]:
        output: list[str] = []
        for name in (*DEFAULT_INSTRUCTION_NAMES, *self.fallback_names):
            if name and name not in output:
                output.append(name)
        return tuple(output)

    def load_entries(self, workspace: str | Path) -> tuple[ProjectInstruction, ...]:
        cwd = Path(workspace).expanduser().resolve()
        root = self._project_root(cwd)
        remaining = self.max_bytes
        entries: list[ProjectInstruction] = []

        for directory in self._directories(root, cwd):
            if remaining <= 0:
                break
            for name in self._candidate_names():
                path = directory / name
                if not path.is_file():
                    continue
                # Match Codex: a discovered instruction file may itself be a
                # symlink. Scope comes from the directory in which it was
                # discovered, not from rejecting targets outside project_root.
                with path.open("rb") as handle:
                    raw = handle.read(remaining + 1)
                truncated = len(raw) > remaining
                payload = raw[:remaining]
                text = payload.decode("utf-8", errors="replace")
                remaining -= len(payload)
                if text.strip():
                    entries.append(ProjectInstruction(path=path, text=text, truncated=truncated))
                # Only the first matching candidate in a directory applies:
                # override > AGENTS.md > configured fallbacks. An empty primary
                # file still wins candidate selection, exactly like Codex.
                break
        return tuple(entries)

    @staticmethod
    def render(entries: Iterable[ProjectInstruction], *, directory: str | Path) -> str:
        selected = tuple(entries)
        if not selected:
            return ""
        # Codex's LoadedAgentsMd::legacy_text joins project entries directly
        # with blank lines. The `--- project-doc ---` separator is only inserted
        # when host/user/thread instructions precede the first project entry;
        # Loom's loader owns project entries only, so it must not invent that
        # transition marker here.
        body = "\n\n".join(entry.text for entry in selected)
        cwd = Path(directory).expanduser().resolve()
        return (
            f"{AGENTS_FRAGMENT_START} for {cwd}\n\n"
            f"<INSTRUCTIONS>\n{body}\n{AGENTS_FRAGMENT_END}"
        )

    def load(self, workspace: str | Path) -> str:
        workspace_path = Path(workspace).expanduser().resolve()
        return self.render(self.load_entries(workspace_path), directory=workspace_path)


__all__ = [
    "AGENTS_FRAGMENT_END",
    "AGENTS_FRAGMENT_START",
    "DEFAULT_INSTRUCTION_NAMES",
    "DEFAULT_ROOT_MARKERS",
    "InstructionLoader",
    "PROJECT_DOC_SEPARATOR",
    "ProjectInstruction",
]
