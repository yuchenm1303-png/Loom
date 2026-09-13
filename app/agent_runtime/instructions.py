"""Codex-parity project instruction discovery, rendering, and applied caching.

Discovery walks from the project root to the effective cwd, choosing at most one
instruction file per directory. Deeper files therefore appear later and can
refine shallower rules. Rendering mirrors Codex's contextual-user fragment
markers so project documentation stays recognisable after history projection.

Codex does not use a turn id as the repository-instruction refresh boundary.
Its AgentsMdManager keeps the applied repository snapshot while the selected
environment/trust authority is unchanged. Loom currently has one local workspace
selection and no independent trust-level contract in this layer, so the closest
faithful representation is an in-memory applied snapshot keyed by resolved
workspace. Restart recovery is intentionally not implemented here; Window 06 owns
reconstruction of the complete captured execution world.
"""
from __future__ import annotations

import threading
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


@dataclass(frozen=True, slots=True)
class ProjectInstructionSnapshot:
    """One applied repository-instruction snapshot for an environment key."""

    workspace: str
    rendered: str
    source_paths: tuple[str, ...]


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
                with path.open("rb") as handle:
                    raw = handle.read(remaining + 1)
                truncated = len(raw) > remaining
                payload = raw[:remaining]
                text = payload.decode("utf-8", errors="replace")
                remaining -= len(payload)
                if text.strip():
                    entries.append(ProjectInstruction(path=path, text=text, truncated=truncated))
                # An existing empty higher-priority candidate still wins this directory.
                break
        return tuple(entries)

    @staticmethod
    def render(entries: Iterable[ProjectInstruction], *, directory: str | Path) -> str:
        selected = tuple(entries)
        if not selected:
            return ""
        body = "\n\n".join(entry.text for entry in selected)
        cwd = Path(directory).expanduser().resolve()
        return (
            f"{AGENTS_FRAGMENT_START} for {cwd}\n\n"
            f"<INSTRUCTIONS>\n{body}\n{AGENTS_FRAGMENT_END}"
        )

    def load(self, workspace: str | Path) -> str:
        workspace_path = Path(workspace).expanduser().resolve()
        return self.render(self.load_entries(workspace_path), directory=workspace_path)


class AppliedInstructionCache:
    """Cache the applied repository snapshot while the environment key is stable.

    Current Loom exposes the resolved workspace as the repository-selection key.
    A future environment/trust owner may widen ``_key`` without changing context
    or compaction semantics. This cache is deliberately process-local: restoring
    an interrupted pending Step is a recovery concern and must restore the whole
    captured execution world, not just AGENTS content.
    """

    def __init__(self, loader: InstructionLoader) -> None:
        self.loader = loader
        self._lock = threading.RLock()
        self._cache: dict[str, ProjectInstructionSnapshot] = {}

    def __getattr__(self, name: str):
        return getattr(self.loader, name)

    @staticmethod
    def _key(workspace: str | Path) -> str:
        return str(Path(workspace).expanduser().resolve())

    def snapshot(self, workspace: str | Path) -> ProjectInstructionSnapshot:
        key = self._key(workspace)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        workspace_path = Path(key)
        entries = self.loader.load_entries(workspace_path)
        snapshot = ProjectInstructionSnapshot(
            workspace=key,
            rendered=self.loader.render(entries, directory=workspace_path),
            source_paths=tuple(str(entry.path) for entry in entries),
        )
        with self._lock:
            # Preserve the first coherent applied value if concurrent requests race.
            return self._cache.setdefault(key, snapshot)

    def load(self, workspace: str | Path) -> str:
        return self.snapshot(workspace).rendered

    def invalidate(self, workspace: str | Path | None = None) -> None:
        """Explicit hook for a future environment/trust-selection owner."""
        with self._lock:
            if workspace is None:
                self._cache.clear()
            else:
                self._cache.pop(self._key(workspace), None)


__all__ = [
    "AGENTS_FRAGMENT_END",
    "AGENTS_FRAGMENT_START",
    "AppliedInstructionCache",
    "DEFAULT_INSTRUCTION_NAMES",
    "DEFAULT_ROOT_MARKERS",
    "InstructionLoader",
    "PROJECT_DOC_SEPARATOR",
    "ProjectInstruction",
    "ProjectInstructionSnapshot",
]
