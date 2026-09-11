"""Projects: the layer above a raw workspace path.

Loom already grouped the conversation list by each thread's workspace, which
gets the shape right but leaves projects existing only as a side effect of
having talked about them. A project you have not started a conversation in
cannot be shown, cannot be named, and cannot be the place a new conversation
begins — the three things a project is actually for.

This module makes a project durable. Its identity is its **root directory**:
one root belongs to exactly one project, which is what keeps "which project is
this thread in?" a question with one answer. The roadmap's multi-root project
is deliberately not built yet, because a thread has exactly one workspace and
multi-root immediately raises "which root does a new thread get?" — a question
worth answering with worktrees in hand rather than guessing now.

Removing a project removes the registration. It never touches files, and never
touches threads; those keep their workspace and simply become unfiled again.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_CONFIG_VERSION = 1
_PROJECT_ID_RE = re.compile(r"^p[a-z0-9]{12}$")

MAX_NAME_LENGTH = 60
MAX_INSTRUCTIONS_LENGTH = 12_000

# The bucket for threads whose workspace no project claims. It is not a
# project: it has no id, cannot be renamed, and cannot be removed.
UNFILED = ""
UNFILED_LABEL = "No project"


class ProjectStoreError(RuntimeError):
    pass


def _default_home() -> Path:
    raw = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".loom").resolve()


def normalize_root(value: str | Path) -> str:
    """One spelling per directory, so a root can be compared as a string.

    Windows makes this load-bearing: ``C:\\Users\\me\\Loom`` and
    ``c:/users/me/loom`` are the same directory, and a registry that treats
    them as two projects would split a conversation list in half.
    """
    root = Path(value).expanduser()
    try:
        root = root.resolve()
    except OSError:  # pragma: no cover - unreachable path on a live filesystem
        root = root.absolute()
    return str(root)


def _root_key(value: str | Path) -> str:
    return os.path.normcase(normalize_root(value))


def default_name(root: str | Path) -> str:
    """What a project is called before anyone renames it."""
    path = Path(normalize_root(root))
    # A drive root has no name of its own; showing "" would be a blank heading.
    return path.name or str(path)


def clean_name(value: str) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME_LENGTH]


def clean_instructions(value: str) -> str:
    """Normalize project instructions without changing the user's wording."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:MAX_INSTRUCTIONS_LENGTH]


@dataclass(frozen=True, slots=True)
class Project:
    """One named place conversations happen."""

    project_id: str
    name: str
    root: str
    created_at: str = ""
    updated_at: str = ""
    instructions: str = ""

    def __post_init__(self) -> None:
        project_id = str(self.project_id or "").strip().casefold()
        if not _PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError(f"invalid project id: {self.project_id!r}")
        root = normalize_root(self.root)
        name = clean_name(self.name) or default_name(root)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "instructions", clean_instructions(self.instructions))

    @property
    def root_key(self) -> str:
        return _root_key(self.root)

    def contains(self, workspace: str | Path | None) -> bool:
        if not workspace:
            return False
        return _root_key(workspace) == self.root_key

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.project_id,
            "name": self.name,
            "root": self.root,
            "instructions": self.instructions,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "Project":
        return cls(
            project_id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            root=str(payload.get("root") or ""),
            created_at=str(payload.get("createdAt") or ""),
            updated_at=str(payload.get("updatedAt") or ""),
            instructions=str(payload.get("instructions") or ""),
        )


class ProjectStore:
    """Durable project registry, stored beside the rest of the Loom home."""

    def __init__(self, home: str | Path | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home is not None else _default_home()
        self.path = self.home / "projects.json"

    # ---- persistence -----------------------------------------------------

    def _read(self) -> dict[str, object]:
        if not self.path.is_file():
            return {"version": _CONFIG_VERSION, "projects": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectStoreError(f"could not read the project registry: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProjectStoreError("project registry root must be a JSON object")
        if int(payload.get("version") or 0) != _CONFIG_VERSION:
            raise ProjectStoreError("unsupported project registry version")
        if not isinstance(payload.get("projects"), list):
            raise ProjectStoreError("project registry projects must be a list")
        return payload

    def _write(self, projects: Iterable[Project]) -> None:
        payload = {
            "version": _CONFIG_VERSION,
            "projects": [project.as_dict() for project in projects],
        }
        self.home.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ProjectStoreError(f"could not save the project registry: {exc}") from exc

    # ---- reads -----------------------------------------------------------

    def list(self) -> tuple[Project, ...]:
        """Every registered project, ordered by name."""
        parsed: list[Project] = []
        for raw in self._read().get("projects") or []:
            if not isinstance(raw, dict):
                raise ProjectStoreError("project entry must be a JSON object")
            try:
                parsed.append(Project.from_dict(raw))
            except (TypeError, ValueError) as exc:
                raise ProjectStoreError(f"invalid project entry: {exc}") from exc
        return tuple(sorted(parsed, key=lambda item: (item.name.casefold(), item.root_key)))

    def get(self, project_id: str) -> Project:
        key = str(project_id or "").strip().casefold()
        for project in self.list():
            if project.project_id == key:
                return project
        raise KeyError(f"unknown project: {project_id!r}")

    def for_workspace(self, workspace: str | Path | None) -> Project | None:
        if not workspace:
            return None
        key = _root_key(workspace)
        for project in self.list():
            if project.root_key == key:
                return project
        return None

    # ---- writes ----------------------------------------------------------

    def create(self, root: str | Path, *, name: str = "", instructions: str = "", now: str = "") -> Project:
        """Register a directory as a project.

        Registering a root that is already a project returns the existing one
        rather than failing: "add this folder" is a request for the folder to
        be present in the list, and it already is.
        """
        resolved = Path(normalize_root(root))
        if not resolved.exists():
            raise ValueError(f"Project folder does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"Project root is not a directory: {resolved}")

        existing = self.for_workspace(resolved)
        if existing is not None:
            return existing

        project = Project(
            project_id="p" + uuid.uuid4().hex[:12],
            name=clean_name(name),
            root=str(resolved),
            created_at=now,
            updated_at=now,
            instructions=clean_instructions(instructions),
        )
        self._write([*self.list(), project])
        return project

    def rename(self, project_id: str, name: str, *, now: str = "") -> Project:
        cleaned = clean_name(name)
        if not cleaned:
            raise ValueError("Project name must not be empty")
        projects = list(self.list())
        for index, project in enumerate(projects):
            if project.project_id == str(project_id or "").strip().casefold():
                renamed = Project(
                    project_id=project.project_id,
                    name=cleaned,
                    root=project.root,
                    created_at=project.created_at,
                    updated_at=now or project.updated_at,
                    instructions=project.instructions,
                )
                projects[index] = renamed
                self._write(projects)
                return renamed
        raise KeyError(f"unknown project: {project_id!r}")

    def set_instructions(self, project_id: str, instructions: str, *, now: str = "") -> Project:
        projects = list(self.list())
        for index, project in enumerate(projects):
            if project.project_id == str(project_id or "").strip().casefold():
                updated = Project(
                    project_id=project.project_id,
                    name=project.name,
                    root=project.root,
                    created_at=project.created_at,
                    updated_at=now or project.updated_at,
                    instructions=clean_instructions(instructions),
                )
                projects[index] = updated
                self._write(projects)
                return updated
        raise KeyError(f"unknown project: {project_id!r}")

    def remove(self, project_id: str) -> Project:
        """Unregister a project. Files and conversations are untouched."""
        key = str(project_id or "").strip().casefold()
        projects = list(self.list())
        remaining = [project for project in projects if project.project_id != key]
        if len(remaining) == len(projects):
            raise KeyError(f"unknown project: {project_id!r}")
        self._write(remaining)
        return next(project for project in projects if project.project_id == key)

    def adopt(self, workspaces: Iterable[str | Path], *, now: str = "") -> tuple[Project, ...]:
        """Register any workspace that no project claims yet.

        This is what makes the feature arrive without a migration step: on the
        first launch the conversation list already groups by workspace, and
        every one of those groups becomes a real project with the same name.
        Skips roots that no longer exist, so a deleted folder does not
        resurrect as an empty heading.
        """
        known = {project.root_key for project in self.list()}
        added: list[Project] = []
        for workspace in workspaces:
            if not workspace:
                continue
            key = _root_key(workspace)
            if key in known:
                continue
            resolved = Path(normalize_root(workspace))
            if not resolved.is_dir():
                continue
            known.add(key)
            added.append(
                Project(
                    project_id="p" + uuid.uuid4().hex[:12],
                    name="",
                    root=str(resolved),
                    created_at=now,
                    updated_at=now,
                )
            )
        if added:
            self._write([*self.list(), *added])
        return tuple(added)


__all__ = [
    "MAX_INSTRUCTIONS_LENGTH",
    "MAX_NAME_LENGTH",
    "UNFILED",
    "UNFILED_LABEL",
    "Project",
    "ProjectStore",
    "ProjectStoreError",
    "clean_instructions",
    "clean_name",
    "default_name",
    "normalize_root",
]
