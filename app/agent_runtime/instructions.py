"""Codex-parity project instruction discovery, rendering, and turn snapshots.

Discovery walks from the project root to the effective cwd, choosing at most one
instruction file per directory. Deeper files therefore appear later and can
refine shallower rules. Rendering mirrors Codex's contextual-user fragment
markers so project documentation stays recognisable after history projection.

Loom uses stateless Chat Completions transports, so it reprojects contextual
fragments on each request. A durable per-turn snapshot prevents that reprojection
from re-reading AGENTS.md during approval resume or later model steps in the same
turn, preserving the frozen instruction state Codex carries in world state.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


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
    session_id: str
    turn_id: str
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


class ProjectInstructionSnapshotStore:
    """Durably freeze model-visible project instructions for one Loom turn."""

    def __init__(self, session_root: str | Path) -> None:
        self.session_root = Path(session_root).expanduser().resolve()

    @staticmethod
    def _turn_key(turn_id: str) -> str:
        return hashlib.sha256(str(turn_id).encode("utf-8")).hexdigest()[:32]

    def _path(self, session_id: str, turn_id: str) -> Path:
        directory = self.session_root / str(session_id) / "instruction_snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"turn-{self._turn_key(turn_id)}.json"

    def load(self, session_id: str, turn_id: str) -> ProjectInstructionSnapshot | None:
        target = self._path(session_id, turn_id)
        if not target.is_file():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("instruction snapshot must be a JSON object")
        if str(payload.get("session_id") or "") != str(session_id):
            raise ValueError("instruction snapshot session id mismatch")
        if str(payload.get("turn_id") or "") != str(turn_id):
            raise ValueError("instruction snapshot turn id mismatch")
        return ProjectInstructionSnapshot(
            session_id=str(session_id),
            turn_id=str(turn_id),
            workspace=str(payload.get("workspace") or ""),
            rendered=str(payload.get("rendered") or ""),
            source_paths=tuple(str(item) for item in payload.get("source_paths", []) if item),
        )

    def capture(
        self,
        *,
        session_id: str,
        turn_id: str,
        workspace: str | Path,
        loader: InstructionLoader,
    ) -> ProjectInstructionSnapshot:
        existing = self.load(session_id, turn_id)
        if existing is not None:
            return existing

        workspace_path = Path(workspace).expanduser().resolve()
        entries = loader.load_entries(workspace_path)
        snapshot = ProjectInstructionSnapshot(
            session_id=str(session_id),
            turn_id=str(turn_id),
            workspace=str(workspace_path),
            rendered=loader.render(entries, directory=workspace_path),
            source_paths=tuple(str(entry.path) for entry in entries),
        )
        target = self._path(session_id, turn_id)
        temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        payload = {
            "version": 1,
            "session_id": snapshot.session_id,
            "turn_id": snapshot.turn_id,
            "workspace": snapshot.workspace,
            "rendered": snapshot.rendered,
            "source_paths": list(snapshot.source_paths),
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return snapshot


class TurnScopedInstructionLoader:
    """Reuse one durable project-instruction snapshot inside an active turn."""

    def __init__(self, loader: InstructionLoader, snapshot_store: ProjectInstructionSnapshotStore) -> None:
        self.loader = loader
        self.snapshot_store = snapshot_store
        self._local = threading.local()

    def __getattr__(self, name: str):
        return getattr(self.loader, name)

    @contextmanager
    def bind_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        workspace: str | Path,
    ) -> Iterator[None]:
        previous = getattr(self._local, "binding", None)
        self._local.binding = (
            str(session_id),
            str(turn_id),
            str(Path(workspace).expanduser().resolve()),
        )
        try:
            yield
        finally:
            self._local.binding = previous

    def load(self, workspace: str | Path) -> str:
        resolved = str(Path(workspace).expanduser().resolve())
        binding = getattr(self._local, "binding", None)
        if binding is None:
            return self.loader.load(resolved)
        session_id, turn_id, bound_workspace = binding
        if resolved != bound_workspace or not turn_id:
            return self.loader.load(resolved)
        return self.snapshot_store.capture(
            session_id=session_id,
            turn_id=turn_id,
            workspace=resolved,
            loader=self.loader,
        ).rendered


__all__ = [
    "AGENTS_FRAGMENT_END",
    "AGENTS_FRAGMENT_START",
    "DEFAULT_INSTRUCTION_NAMES",
    "DEFAULT_ROOT_MARKERS",
    "InstructionLoader",
    "PROJECT_DOC_SEPARATOR",
    "ProjectInstruction",
    "ProjectInstructionSnapshot",
    "ProjectInstructionSnapshotStore",
    "TurnScopedInstructionLoader",
]
